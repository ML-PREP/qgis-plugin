import sys
import argparse
import os
import json
import numpy as np
import shapely.geometry
import rasterio
import rasterio.mask
import geopandas as gpd
from rasterio import features
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.windows import from_bounds

# Suppress TensorFlow logging spam
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
import tensorflow as tf

def run_dl_landslide_detection(dem_path, after_path, lulc_path, output_path, 
                               roi_path, filter_col, filter_val, model_path, stats_path):
    TARGET_CRS = "EPSG:4326"
    METRIC_CRS = "EPSG:32651" 
    BUFFER_METERS = 10
    PATCH_SIZE = 128
    OVERLAP = 0.5

    # ==========================================
    # 1. EXTRACT ROI GEOMETRY (MAKILALA)
    # ==========================================
    roi_geom = None
    roi_crs = None
    if roi_path and str(roi_path).lower() != 'none' and os.path.exists(roi_path):
        try:
            gdf = gpd.read_file(roi_path)
            if filter_col and filter_val:
                gdf = gdf[gdf[filter_col] == filter_val]
            if not gdf.empty:
                roi_geom = gdf.geometry.unary_union
                roi_crs = gdf.crs
        except Exception as e:
            return {"status": "error", "message": f"ROI Loading Error: {str(e)}"}

    # ==========================================
    # 2. CROP SENTINEL-2 STRICTLY TO ROI
    # ==========================================
    with rasterio.open(after_path) as src_after:
        s2_crs = src_after.crs
        
        if roi_geom is not None:
            geom_s2 = gpd.GeoSeries([roi_geom], crs=roi_crs).to_crs(s2_crs).iloc[0]
            s2_img_raw, s2_transform = rasterio.mask.mask(src_after, [geom_s2], crop=True, nodata=np.nan, filled=True)
        else:
            with rasterio.open(dem_path) as src_dem:
                s2_bbox = transform_bounds(src_dem.crs, s2_crs, *src_dem.bounds)
                window = from_bounds(*s2_bbox, transform=src_after.transform)
                window = window.round_lengths().round_offsets()
                s2_img_raw = src_after.read(window=window, boundless=True, fill_value=0)
                s2_transform = src_after.window_transform(window)

        after_meta = src_after.meta.copy()
        after_meta.update({
            "driver": "GTiff",
            "height": s2_img_raw.shape[1],
            "width": s2_img_raw.shape[2],
            "transform": s2_transform
        })
        after_crs = src_after.crs

    if s2_img_raw.shape[0] < 12:
        return {"status": "error", "message": f"Expected 12 bands in Sentinel-2 image, got {s2_img_raw.shape[0]}"}

    # ==========================================
    # 3. REPROJECT DEM AND CALCULATE NATIVE UNITS
    # ==========================================
    try:
        with rasterio.open(dem_path) as src_dem:
            dem_img = np.zeros((s2_img_raw.shape[1], s2_img_raw.shape[2]), dtype=np.float32)
            reproject(
                source=src_dem.read(1),
                destination=dem_img,
                src_transform=src_dem.transform,
                src_crs=src_dem.crs,
                dst_transform=s2_transform,
                dst_crs=after_crs,
                resampling=Resampling.bilinear
            )
            
        res_x, res_y = abs(s2_transform[0]), abs(s2_transform[4])
        scale_factor = 111320 if after_crs.to_epsg() == 4326 else 1
        dy, dx = np.gradient(dem_img, res_y * scale_factor, res_x * scale_factor)
        
        # Calculate Slope in Degrees (Reference Script Requirement)
        slope_deg = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
        
        # Apply L4S Native Unit Scaling
        slope_scaled = slope_deg / 10.0
        dem_scaled = dem_img / 1000.0

        # Apply Per-Band Mean to S2 (Reference Script Requirement)
        s2_img_scaled = np.zeros_like(s2_img_raw[:12], dtype=np.float32)
        for i in range(12):
            band = s2_img_raw[i]
            valid = band[np.isfinite(band) & (band > 0)]
            mean_val = valid.mean() if valid.size > 0 else 1.0
            s2_img_scaled[i] = np.nan_to_num(band) / mean_val

    except Exception as e:
        return {"status": "error", "message": f"DEM/Slope Processing Error: {str(e)}"}

    # ==========================================
    # 4. BUILD TENSOR AND NORMALIZE
    # ==========================================
    try:
        input_array = np.vstack([s2_img_scaled, slope_scaled[np.newaxis, :, :], dem_scaled[np.newaxis, :, :]])
        input_array = np.transpose(input_array, (1, 2, 0))
        input_array = np.nan_to_num(input_array, nan=0.0)

        with open(stats_path, 'r', encoding='utf-8') as f:
            stats = json.load(f)
        mean_arr = np.array(stats["mean"], dtype=np.float32)
        std_arr = np.array(stats["std"], dtype=np.float32)
        
        input_array = (input_array - mean_arr) / std_arr
    except Exception as e:
        return {"status": "error", "message": f"Data stacking error: {str(e)}"}

    # ==========================================
    # 5. INFERENCE WITH HANN-WINDOW BLENDING
    # ==========================================
    try:
        model = tf.keras.models.load_model(model_path, compile=False)
        H, W = input_array.shape[:2]
        
        # Create 2D Hann window for smooth overlap blending
        win_1d = np.hanning(PATCH_SIZE + 2)[1:-1]
        win_2d = np.outer(win_1d, win_1d).astype(np.float32)

        prob_acc = np.zeros((H, W), np.float32)
        weight_acc = np.zeros((H, W), np.float32)

        stride = max(1, int(PATCH_SIZE * (1.0 - OVERLAP)))
        rows = sorted(set(list(range(0, max(1, H - PATCH_SIZE + 1), stride)) + [max(0, H - PATCH_SIZE)]))
        cols = sorted(set(list(range(0, max(1, W - PATCH_SIZE + 1), stride)) + [max(0, W - PATCH_SIZE)]))

        for r in rows:
            for c in cols:
                h = min(PATCH_SIZE, H - r)
                w = min(PATCH_SIZE, W - c)
                
                tile = input_array[r:r+h, c:c+w, :]
                if (h, w) != (PATCH_SIZE, PATCH_SIZE):
                    tile = np.pad(tile, ((0, PATCH_SIZE - h), (0, PATCH_SIZE - w), (0, 0)), mode="reflect")
                
                # Predict (Assuming output is either binary mask or categorical probabilities)
                preds = model.predict(tile[np.newaxis, ...].astype(np.float32), verbose=0)[0]
                
                if preds.shape[-1] == 1:
                    prob = preds[..., 0]
                else:
                    prob = tf.nn.softmax(preds, axis=-1)[..., 1].numpy() if preds.shape[-1] > 1 else preds[..., 0]
                
                # Blend probabilities seamlessly
                prob_acc[r:r+h, c:c+w] += (prob * win_2d)[:h, :w]
                weight_acc[r:r+h, c:c+w] += win_2d[:h, :w]

        # Finalize merged predictions
        final_prob = prob_acc / np.maximum(weight_acc, 1e-6)
        final_prediction = (final_prob > 0.5).astype(np.uint8)
        
    except Exception as e:
        return {"status": "error", "message": f"AI Inference Error: {str(e)}"}

    # ==========================================
    # 6. LULC MASKING
    # ==========================================
    if lulc_path and str(lulc_path).lower() != 'none' and os.path.exists(lulc_path):
        try:
            landcover_gdf = gpd.read_file(lulc_path).to_crs(s2_crs)
            exclude_classes = ["Built-up", "Inland Water", "Marshland/Swamp", "Open/Barren", "Annual Crop"]
            col_name = next((c for c in ['Class_Name', 'CLASS_NAME', 'type', 'TYPE', 'class_name'] if c in landcover_gdf.columns), None)
            
            if col_name:
                mask_out_gdf = landcover_gdf[landcover_gdf[col_name].isin(exclude_classes)]
                if not mask_out_gdf.empty:
                    mask_out_gdf = mask_out_gdf.to_crs(METRIC_CRS)
                    mask_out_gdf['geometry'] = mask_out_gdf.geometry.buffer(BUFFER_METERS)
                    mask_out_gdf = mask_out_gdf.to_crs(s2_crs)
                    
                    if not mask_out_gdf.empty:
                        lc_mask = features.geometry_mask(
                            mask_out_gdf.geometry,
                            out_shape=(H, W),
                            transform=s2_transform,
                            invert=True 
                        )
                        final_prediction[lc_mask] = 0
        except Exception:
            pass 

    # ==========================================
    # 7. SAVE RESULTS
    # ==========================================
    after_meta.update({"count": 1, "dtype": 'uint8', "nodata": 0})
    with rasterio.open(output_path, "w", **after_meta) as dst:
        dst.write(final_prediction, 1)

    return {
        "status": "success", 
        "output_mask": output_path
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dem", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--lulc", required=False, default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument("--roi_path", required=False, default=None)
    parser.add_argument("--filter_col", required=False, default=None)
    parser.add_argument("--filter_val", required=False, default=None)
    parser.add_argument("--model_path", required=True)
    parser.add_argument("--stats_path", required=True)
    
    args = parser.parse_args()
    
    try:
        result = run_dl_landslide_detection(
            args.dem, args.after, args.lulc, args.output,
            args.roi_path, args.filter_col, args.filter_val,
            args.model_path, args.stats_path
        )
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))