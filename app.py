# shiny-folium app for Ghana mining licenses with satellite imagery overlays
# -----------------------------------------------
from shiny import App, Inputs, Outputs, Session, render, ui
import folium
from folium import raster_layers
import geopandas as gpd
import pandas as pd
import urllib.parse
import json
import os
import base64
import pyproj

# Define base data directory relative to script location
# This assumes your directory structure is:
# project_folder/
#   ├── app.py (this file)
#   ├── data/
#   │   ├── licenses/
#   │   │   └── ghana_small_scale_gold_licenses.geojson
#   │   ├── boundaries/
#   │   │   ├── gadm41_GHA_1.json
#   │   │   └── gadm41_GHA_2.json
#   │   ├── detections/
#   │   │   └── ashanti_detections_polygons.geojson
#   │   └── analysis/
#   │       └── ashanti_districts_mp_mining_ratio.geojson
#   ├── www/
#   │   └── (satellite PNG files)
#   └── satellite_bounds_test.json

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Define relative paths to data directories
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
LICENSES_DIR = os.path.join(DATA_DIR, "licenses")
BOUNDARIES_DIR = os.path.join(DATA_DIR, "boundaries")
DETECTIONS_DIR = os.path.join(DATA_DIR, "detections")
ANALYSIS_DIR = os.path.join(DATA_DIR, "analysis")
SATELLITE_PNG_DIR = os.path.join(SCRIPT_DIR, "www")

# Define file paths
LICENSES_FILE = os.path.join(LICENSES_DIR, "ghana_small_scale_gold_licenses.geojson")
REGIONS_FILE = os.path.join(BOUNDARIES_DIR, "gadm41_GHA_1.json")
DISTRICTS_FILE = os.path.join(BOUNDARIES_DIR, "gadm41_GHA_2.json")
DETECTIONS_FILE = os.path.join(DETECTIONS_DIR, "ashanti_detections_polygons.geojson")
DISTRICT_RATIOS_FILE = os.path.join(ANALYSIS_DIR, "ashanti_districts_mp_mining_ratio.geojson")
SATELLITE_BOUNDS_FILE = os.path.join(SCRIPT_DIR, "satellite_bounds_test.json")

# Check if files exist and provide helpful error messages
def check_file_exists(filepath, description):
    if not os.path.exists(filepath):
        print(f"WARNING: {description} not found at: {filepath}")
        print(f"  Expected relative to: {SCRIPT_DIR}")
        return False
    return True

# Verify all required files
files_to_check = [
    (LICENSES_FILE, "License data"),
    (REGIONS_FILE, "Region boundaries"),
    (DISTRICTS_FILE, "District boundaries"),
    (DETECTIONS_FILE, "Mining detections"),
    (DISTRICT_RATIOS_FILE, "District mining ratios"),
]

all_files_present = True
for filepath, desc in files_to_check:
    if not check_file_exists(filepath, desc):
        all_files_present = False

if not all_files_present:
    print("\nPlease ensure your directory structure matches the expected layout:")
    print("project_folder/")
    print("  ├── app.py (this file)")
    print("  ├── data/")
    print("  │   ├── licenses/")
    print("  │   ├── boundaries/")
    print("  │   ├── detections/")
    print("  │   └── analysis/")
    print("  ├── www/")
    print("  └── satellite_bounds_test.json")

# Load data
try:
    gdf_licenses = gpd.read_file(LICENSES_FILE)
    gdf_regions = gpd.read_file(REGIONS_FILE)
    gdf_districts = gpd.read_file(DISTRICTS_FILE)
    gdf_detections = gpd.read_file(DETECTIONS_FILE)
    gdf_district_ratios = gpd.read_file(DISTRICT_RATIOS_FILE)
except Exception as e:
    print(f"Error loading data files: {e}")
    raise

# Set up coordinate transformer
utm_crs = pyproj.CRS('EPSG:32630')  # UTM Zone 30N
wgs84_crs = pyproj.CRS('EPSG:4326')  # WGS84 (lat/lon)
transformer = pyproj.Transformer.from_crs(utm_crs, wgs84_crs, always_xy=True)

# Load bounds data if available
satellite_bounds = {}
if os.path.exists(SATELLITE_BOUNDS_FILE):
    with open(SATELLITE_BOUNDS_FILE, 'r') as f:
        satellite_bounds_utm = json.load(f)
    
    # Convert UTM bounds to lat/lon
    for img_name, bounds_utm in satellite_bounds_utm.items():
        # Transform corners from UTM to lat/lon
        west_lon, south_lat = transformer.transform(bounds_utm['west'], bounds_utm['south'])
        east_lon, north_lat = transformer.transform(bounds_utm['east'], bounds_utm['north'])
        
        satellite_bounds[img_name] = {
            'north': north_lat,
            'south': south_lat,
            'east': east_lon,
            'west': west_lon,
            'north_utm': bounds_utm['north'],
            'south_utm': bounds_utm['south'],
            'east_utm': bounds_utm['east'],
            'west_utm': bounds_utm['west']
        }
    
    print(f"Loaded and converted {len(satellite_bounds)} satellite bounds from UTM to WGS84")
else:
    print(f"Warning: {SATELLITE_BOUNDS_FILE} not found")

bounds = gdf_licenses.total_bounds
center_lat = (bounds[1] + bounds[3]) / 2
center_lon = (bounds[0] + bounds[2]) / 2

# ── UI ───────────────────────────────────────────────────────────────
app_ui = ui.page_fluid(
    ui.h3("Ghana Artisinal Small-scale Gold Mining Monitoring Dashboard"),
    ui.output_ui("map"),
    # Add custom JavaScript to check image loading and handle legend
    ui.tags.script("""
        // Monitor image loading
        window.addEventListener('load', function() {
            setTimeout(function() {
                var images = document.querySelectorAll('img.leaflet-image-layer');
                console.log('Found ' + images.length + ' leaflet image layers');
                images.forEach(function(img, i) {
                    console.log('Image ' + i + ':', img.src.substring(0, 100) + '...', 
                               'Complete:', img.complete, 
                               'Natural size:', img.naturalWidth + 'x' + img.naturalHeight);
                });
            }, 2000);
        });
    """)
)

# ── server ───────────────────────────────────────────────────────────
def server(input: Inputs, output: Outputs, session: Session):

    @output
    @render.ui
    def map():
        m = folium.Map(
            location=[center_lat, center_lon], 
            zoom_start=8,
            width="100%", 
            height="80vh"
        )

        # Planet basemap (as fallback/reference)
        api_key = "PLAKa82bdaf836f64dc68c898cd3a363101c"
        planet_url = (
            "https://tiles.planet.com/basemaps/v1/planet-tiles/"
            "global_monthly_2024_01_mosaic/gmap/{z}/{x}/{y}.png"
            f"?api_key={api_key}"
        )
        folium.TileLayer(
            planet_url, 
            name="Planet Basemap", 
            attr="Planet Labs",
            overlay=False,
            control=True, 
            show=False
        ).add_to(m)

        # --- Satellite Image Overlays -------------------------------------
        # Create a feature group for satellite images
        satellite_grp = folium.FeatureGroup(name="Satellite Images (High-Res)", show=False)
        
        images_added = 0
        
        # Add each satellite image as an overlay
        for image_name, bounds_data in satellite_bounds.items():
            # Try different resolution options
            img_path = None
            res_label = None
            
            # Check for different resolution files
            for res, label in [("med", "Medium"), ("low", "Low"), ("", "Original")]:
                suffix = f"_{res}" if res else ""
                test_path = os.path.join(SATELLITE_PNG_DIR, f"{image_name}{suffix}.png")
                if os.path.exists(test_path):
                    img_path = test_path
                    res_label = label
                    break
            
            if not img_path:
                print(f"Warning: No image found for {image_name}")
                continue
            
            # Create bounds array for Folium
            img_bounds = [
                [bounds_data['south'], bounds_data['west']],
                [bounds_data['north'], bounds_data['east']]
            ]
            
            print(f"Adding image: {img_path}")
            print(f"  UTM bounds: N:{bounds_data['north_utm']:.1f}, S:{bounds_data['south_utm']:.1f}, E:{bounds_data['east_utm']:.1f}, W:{bounds_data['west_utm']:.1f}")
            print(f"  WGS84 bounds: {img_bounds}")
            
            # Method 1: Try with base64 encoding (more reliable for local files)
            try:
                with open(img_path, 'rb') as f:
                    img_data = f.read()
                    img_base64 = base64.b64encode(img_data).decode()
                    img_url = f"data:image/png;base64,{img_base64}"
                
                img_overlay = raster_layers.ImageOverlay(
                    image=img_url,
                    bounds=img_bounds,
                    opacity=0.8,  # Slightly transparent to see basemap
                    name=f"Sat: {image_name[:15]}... ({res_label})",
                    interactive=True,
                    cross_origin=False
                )
                img_overlay.add_to(satellite_grp)
                images_added += 1
                
            except Exception as e:
                print(f"Error adding image {img_path}: {e}")
                # Fallback: try with relative path from www
                try:
                    # For Shiny, use relative path from www directory
                    relative_path = os.path.basename(img_path)
                    
                    img_overlay = raster_layers.ImageOverlay(
                        image=relative_path,  # Just the filename, Shiny serves from www
                        bounds=img_bounds,
                        opacity=0.8,
                        name=f"Sat: {image_name[:15]}... ({res_label})",
                        interactive=True
                    )
                    img_overlay.add_to(satellite_grp)
                    images_added += 1
                except Exception as e2:
                    print(f"Fallback also failed: {e2}")
        
        print(f"Total images added: {images_added}")
        satellite_grp.add_to(m)

        # --- Region boundaries (Level 1) - NOW WITH SUBTLE FILL -----------
        region_grp = folium.FeatureGroup(name="Regions", show=False)
        for _, row in gdf_regions.iterrows():
            region_name = row.get('NAME_1', 'Unknown')
            
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda *_: {
                    "fillColor": "#FF6B6B",  # Light red fill
                    "color": "#FF0000",       # Red border
                    "weight": 3,
                    "opacity": 0.8,
                    "fillOpacity": 0.1,       # Very subtle fill
                    "dashArray": "5, 5"
                },
                highlight_function=lambda *_: {
                    "fillColor": "#FF6B6B",
                    "color": "#FF0000",
                    "weight": 4,
                    "fillOpacity": 0.3,       # More visible on hover
                },
                tooltip=folium.Tooltip(f"<b>Region:</b> {region_name}", sticky=True),
            ).add_to(region_grp)
        region_grp.add_to(m)

        # --- District Mining Ratios (NEW LAYER) -----------------------------
        district_ratio_grp = folium.FeatureGroup(name="Districts by Mining Ratio", show=False)
        
        for _, row in gdf_district_ratios.iterrows():
            # Get properties
            props = row.to_dict()
            
            # Calculate values
            excess = props.get('excess_mining_ha', 0)
            excess_color = 'red' if excess > 0 else 'green'
            ratio_text = 'No licensed mining' if props['mining_ratio'] == -1 else f"{props['mining_ratio']:.2f}"
            
            # Create clean popup content
            popup_html = f"""
            <div style='font-family: Arial; font-size: 14px; width: 280px;'>
                <h4 style='margin: 0 0 8px 0;'>{props['district_name']}</h4>
                
                <div style='background: #f5f5f5; padding: 8px; margin-bottom: 8px; border-radius: 4px;'>
                    <b>{props['mp_name']}</b> ({props['party']})<br>
                    <small>{props['constituency']}</small>
                    {f'<br><a href="{props["profile_link"]}" target="_blank">View Profile →</a>' if props.get('profile_link') else ''}
                </div>
                
                <div style='margin-top: 8px;'>
                    <table style='width: 100%; border-collapse: collapse;'>
                        <tr>
                            <td style='padding: 4px 0;'>Licensed:</td>
                            <td style='text-align: right; padding: 4px 0;'><b>{props['licensed_mining_ha']} ha</b></td>
                        </tr>
                        <tr>
                            <td style='padding: 4px 0;'>Detected:</td>
                            <td style='text-align: right; padding: 4px 0;'><b>{props['detected_mining_ha']} ha</b></td>
                        </tr>
                        <tr>
                            <td style='padding: 4px 0; border-top: 1px solid #ddd;'>Unlicensed:</td>
                            <td style='text-align: right; padding: 4px 0; border-top: 1px solid #ddd; color: {excess_color};'><b>{excess} ha</b></td>
                        </tr>
                    </table>
                </div>
            </div>
            """
            
            # Simple tooltip - show district name and ratio
            tooltip_text = f"{props['district_name']}: {ratio_text}"
            
            # Handle transparent color
            fill_color = props['fill_color']
            fill_opacity = 0.6 if fill_color != 'transparent' else 0
            
            # Add the district polygon
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda x, fill_color=fill_color, fill_opacity=fill_opacity: {
                    'fillColor': fill_color if fill_color != 'transparent' else '#000000',
                    'color': 'black',
                    'weight': 1,
                    'fillOpacity': fill_opacity,
                },
                popup=folium.Popup(popup_html, max_width=400),
                tooltip=folium.Tooltip(tooltip_text)
            ).add_to(district_ratio_grp)
        
        district_ratio_grp.add_to(m)

        # --- District boundaries (Level 2) - NOW WITH SUBTLE FILL ----------
        district_grp = folium.FeatureGroup(name="District Boundaries", show=False)
        for _, row in gdf_districts.iterrows():
            district_name = row.get('NAME_2', 'Unknown')
            region_name = row.get('NAME_1', 'Unknown')
            
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda *_: {
                    "fillColor": "#FFB366",   # Light orange fill
                    "color": "#FFA500",       # Orange border
                    "weight": 1.5,
                    "opacity": 0.7,
                    "fillOpacity": 0.1        # Very subtle fill
                },
                highlight_function=lambda *_: {
                    "fillColor": "#FFB366",
                    "color": "#FFA500",
                    "weight": 2.5,
                    "fillOpacity": 0.3        # More visible on hover
                },
                tooltip=folium.Tooltip(f"<b>District:</b> {district_name}<br><b>Region:</b> {region_name}", sticky=True),
            ).add_to(district_grp)
        district_grp.add_to(m)

        # --- Detection polygons --------------------------------------------
        detection_grp = folium.FeatureGroup(name="Detected Mining Activity", show=False)

        # Create color scale based on date
        dates = pd.to_datetime(gdf_detections['date'], format='%Y%m%d')
        date_min = dates.min()
        date_range = (dates.max() - date_min).days

        for _, row in gdf_detections.iterrows():
            # Calculate color based on date (newer = redder)
            if date_range == 0:
                fill_color = '#800000'  # Fixed medium red
            else:
                date_val = pd.to_datetime(row['date'], format='%Y%m%d')
                days_since_min = (date_val - date_min).days
                color_intensity = int(255 * (days_since_min / date_range))
                fill_color = f'#{color_intensity:02x}0000'
            
            tooltip_html = f"""
            <div style='font-family: Arial; font-size: 12px;'>
                <b>Detected Activity</b><br>
                <b>Date:</b> {row['date']}<br>
                <b>Detections:</b> {row['n_detections']}
            </div>
            """
            
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda feature, fc=fill_color: {
                    "fillColor": fc,
                    "color": "#8B0000",
                    "weight": 1.5,
                    "fillOpacity": 0.6,
                },
                tooltip=folium.Tooltip(tooltip_html, sticky=True),
            ).add_to(detection_grp)
        detection_grp.add_to(m)

        # --- license polygons ----------------------------------------
        lic_grp = folium.FeatureGroup(name="Licensed sites", show=False)
        for _, row in gdf_licenses.iterrows():
            # Format dates
            start_date = str(row["start_date"])[:10] if pd.notna(row["start_date"]) else "N/A"
            end_date = str(row["expiry_date"])[:10] if pd.notna(row["expiry_date"]) else "N/A"
            
            # Create tooltip HTML
            tooltip_html = f"""
            <div style='font-family: Arial; font-size: 12px;'>
                <b>{row['lic_code']}</b><br>
                <b>Owner:</b> {row['owner_name']}<br>
                <b>Type:</b> {row['type']}<br>
                <b>Start Date:</b> {start_date}<br>
                <b>End Date:</b> {end_date}<br>
                <b>Acreage:</b> {row['area_ha']:.2f} ha<br>
                <b>Address:</b> {row['owner_address']}<br>
                <b>City:</b> {row['owner_city']}
            </div>
            """
            
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda *_: {
                    "fillColor": "#0000FF",
                    "color": "#000080",
                    "weight": 2,
                    "fillOpacity": 0.3,
                },
                tooltip=folium.Tooltip(tooltip_html, sticky=True),
            ).add_to(lic_grp)
        lic_grp.add_to(m)

        # Add legend for mining compliance - initially hidden
        legend_html = '''
        <div id="mining-legend" style="position: fixed; 
                    bottom: 150px; right: 50px; width: 280px; 
                    background-color: white; border:2px solid grey; z-index:9999; 
                    font-size:14px; padding: 15px; display: none;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.2); border-radius: 8px;">
        <h4 style="margin-top: 0; margin-bottom: 10px; color: #333;">Mining Compliance</h4>
        <p style="margin: 5px 0 10px 0; font-size: 12px; color: #666;"><b>Unlicensed mining area (hectares)</b></p>
        <div style="margin-top: 10px;">
            <div style="display: flex; align-items: center; margin: 8px 0;">
                <div style="width: 25px; height: 18px; background-color: transparent; margin-right: 10px; border: 1px solid #ccc; border-radius: 3px;"></div>
                <span style="font-size: 13px;">No mining activity</span>
            </div>
            <div style="display: flex; align-items: center; margin: 8px 0;">
                <div style="width: 25px; height: 18px; background-color: #00ff00; margin-right: 10px; border: 1px solid #ccc; border-radius: 3px;"></div>
                <span style="font-size: 13px;">Compliant (no excess)</span>
            </div>
            <div style="display: flex; align-items: center; margin: 8px 0;">
                <div style="width: 25px; height: 18px; background-color: #ffff00; margin-right: 10px; border: 1px solid #ccc; border-radius: 3px;"></div>
                <span style="font-size: 13px;">&lt; 50 ha unlicensed</span>
            </div>
            <div style="display: flex; align-items: center; margin: 8px 0;">
                <div style="width: 25px; height: 18px; background-color: #ff8000; margin-right: 10px; border: 1px solid #ccc; border-radius: 3px;"></div>
                <span style="font-size: 13px;">50-200 ha unlicensed</span>
            </div>
            <div style="display: flex; align-items: center; margin: 8px 0;">
                <div style="width: 25px; height: 18px; background-color: #ff0000; margin-right: 10px; border: 1px solid #ccc; border-radius: 3px;"></div>
                <span style="font-size: 13px;">200-500 ha unlicensed</span>
            </div>
            <div style="display: flex; align-items: center; margin: 8px 0;">
                <div style="width: 25px; height: 18px; background-color: #8b0000; margin-right: 10px; border: 1px solid #ccc; border-radius: 3px;"></div>
                <span style="font-size: 13px;">&gt; 500 ha unlicensed</span>
            </div>
        </div>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(legend_html))

        # Add JavaScript to handle legend visibility based on layer selection
        legend_js = '''
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Wait for the map to be fully loaded
            setTimeout(function() {
                // Find all layer control checkboxes
                var checkboxes = document.querySelectorAll('.leaflet-control-layers-overlays input[type="checkbox"]');
                var legend = document.getElementById('mining-legend');
                
                // Function to check if Districts by Mining Ratio is checked
                function updateLegendVisibility() {
                    var showLegend = false;
                    checkboxes.forEach(function(checkbox) {
                        var label = checkbox.nextSibling.textContent.trim();
                        if (label === 'Districts by Mining Ratio' && checkbox.checked) {
                            showLegend = true;
                        }
                    });
                    
                    if (showLegend) {
                        legend.style.display = 'block';
                    } else {
                        legend.style.display = 'none';
                    }
                }
                
                // Add event listeners to all checkboxes
                checkboxes.forEach(function(checkbox) {
                    checkbox.addEventListener('change', updateLegendVisibility);
                });
                
                // Initial check
                updateLegendVisibility();
            }, 1000);
        });
        </script>
        '''
        m.get_root().html.add_child(folium.Element(legend_js))

        # Add layer control
        folium.LayerControl(collapsed=False, position='topright').add_to(m)

        # fit view
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

        return ui.HTML(m._repr_html_())

app = App(app_ui, server)