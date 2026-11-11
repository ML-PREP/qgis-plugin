package main

import (
	"strings"
	"testing"
)

func TestStatRuleFor(t *testing.T) {
	cases := []struct {
		name     string
		rule     string
		resample string
	}{
		// The QGIS plugin stages the soil-type raster as "type.tif".
		{"type", "majority", "nearest"},
		{"SoilType", "majority", "nearest"},
		{"LULC", "majority", "nearest"},
		{"DistanceToFault", "min", "bilinear"},
		{"DistanceToRoad", "min", "bilinear"},
		{"DistanceToRiver", "min", "bilinear"},
		{"Slope", "mean", "bilinear"},
		{"BUK", "mean", "bilinear"},
		{"PGA2", "mean", "bilinear"},
	}
	for _, c := range cases {
		rule, resample := statRuleFor(c.name)
		if rule != c.rule || resample != c.resample {
			t.Errorf("statRuleFor(%q) = (%q, %q), want (%q, %q)",
				c.name, rule, resample, c.rule, c.resample)
		}
	}
}

func TestZonalGridScriptIsEmbedded(t *testing.T) {
	for _, command := range []string{"def build_zones", "def zonal_stats_on_grid", `"zones"`, `"stats"`} {
		if !strings.Contains(zonalGridScript, command) {
			t.Errorf("embedded zonal_grid.py lacks %s", command)
		}
	}
}
