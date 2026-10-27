cmake_minimum_required(VERSION 3.20)
project(step4_deposition_zone_mpi LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(
    TAUDEM_SRC_DIR
    "${CMAKE_CURRENT_SOURCE_DIR}/third_party/taudem/src"
    CACHE PATH
    "Path to the TauDEM source subset used by Step 4"
)

find_package(MPI REQUIRED)

find_package(GDAL CONFIG QUIET)
if(NOT GDAL_FOUND)
    find_package(GDAL REQUIRED)
endif()

add_executable(step4_deposition_zone_mpi
    step4_deposition_zone_mpi.cpp
    "${TAUDEM_SRC_DIR}/commonLib.cpp"
    "${TAUDEM_SRC_DIR}/tiffIO.cpp"
)

target_include_directories(step4_deposition_zone_mpi PRIVATE
    "${TAUDEM_SRC_DIR}"
    ${MPI_CXX_INCLUDE_DIRS}
)

target_link_libraries(step4_deposition_zone_mpi PRIVATE
    MPI::MPI_CXX
)

if(TARGET GDAL::GDAL)
    target_link_libraries(step4_deposition_zone_mpi PRIVATE GDAL::GDAL)
else()
    target_include_directories(step4_deposition_zone_mpi PRIVATE ${GDAL_INCLUDE_DIR})
    target_link_libraries(step4_deposition_zone_mpi PRIVATE ${GDAL_LIBRARY})
endif()

if(MSVC)
    target_compile_definitions(step4_deposition_zone_mpi PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

# The TauDEM tools the plugin runs before Step 4: pit filling, D-infinity
# flow direction, and the ML-DFI predictor chain. Shipping them with the engine removes TauDEM as
# a separate install. Off by default; build_native.sh enables it for macOS and Linux.
option(MLPREP_BUILD_TAUDEM_TOOLS "Build pitremove and dinfflowdir" OFF)

function(add_taudem_tool name)
    add_executable(${name} ${ARGN}
        "${TAUDEM_SRC_DIR}/commonLib.cpp"
        "${TAUDEM_SRC_DIR}/tiffIO.cpp"
    )
    target_include_directories(${name} PRIVATE "${TAUDEM_SRC_DIR}")
    target_link_libraries(${name} PRIVATE MPI::MPI_CXX)
    if(TARGET GDAL::GDAL)
        target_link_libraries(${name} PRIVATE GDAL::GDAL)
    else()
        target_include_directories(${name} PRIVATE ${GDAL_INCLUDE_DIR})
        target_link_libraries(${name} PRIVATE ${GDAL_LIBRARY})
    endif()
    if(MSVC)
        target_compile_definitions(${name} PRIVATE _CRT_SECURE_NO_WARNINGS)
    endif()
endfunction()

if(MLPREP_BUILD_TAUDEM_TOOLS)
    add_taudem_tool(pitremove
        "${TAUDEM_SRC_DIR}/PitRemovemn.cpp"
        "${TAUDEM_SRC_DIR}/flood.cpp"
    )
    add_taudem_tool(dinfflowdir
        "${TAUDEM_SRC_DIR}/DinfFlowDirmn.cpp"
        "${TAUDEM_SRC_DIR}/dinf.cpp"
        "${TAUDEM_SRC_DIR}/Node.cpp"
    )
    # The ML-DFI predictor chain: catchment area, stream threshold and
    # flow-path distances down to streams and up to ridges.
    add_taudem_tool(areadinf
        "${TAUDEM_SRC_DIR}/areadinfmn.cpp"
        "${TAUDEM_SRC_DIR}/areadinf.cpp"
        "${TAUDEM_SRC_DIR}/ReadOutlets.cpp"
    )
    add_taudem_tool(threshold
        "${TAUDEM_SRC_DIR}/Thresholdmn.cpp"
        "${TAUDEM_SRC_DIR}/Threshold.cpp"
    )
    add_taudem_tool(dinfdistdown
        "${TAUDEM_SRC_DIR}/DinfDistDownmn.cpp"
        "${TAUDEM_SRC_DIR}/DinfDistDown.cpp"
    )
    add_taudem_tool(dinfdistup
        "${TAUDEM_SRC_DIR}/DinfDistUpmn.cpp"
        "${TAUDEM_SRC_DIR}/DinfDistUp.cpp"
    )
endif()
