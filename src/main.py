import json
import os
import boto3
import base64  # Added missing import for PNG encoding 
import rasterio
import geopandas as gpd
import numpy as np
from rasterio.warp import transform_bounds

# --- NEW: Typing and Validation Imports ---
from typing import Dict, Any, Tuple, Optional, List
from pydantic import BaseModel, validate_call, ValidationError

# Fixed: Added trailing comma to make this a proper tuple for .endswith()
VECTOR_FILES: Tuple[str, ...] = ('.geojson', '.gpkg', '.shp')
RASTER_FILES: Tuple[str, ...] = ('.tif',)

# Initialize the S3 client outside the handler
s3 = boto3.client('s3')

# We'll get this from our Docker run command (locally)
# or Lambda configuration (in AWS)
BUCKET_NAME: Optional[str] = os.environ.get('S3_BUCKET_NAME')


# --- NEW: Pydantic Model for the Lambda Event ---
class ApiGatewayEvent(BaseModel):
    pathParameters: Optional[Dict[str, str]] = None


@validate_call
def get_s3_file_structure(bucket_name: str, folder_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Lists S3 objects under a prefix and returns a nested dictionary
    representing the exact folder structure.
    - Keys are folder/file names.
    - Values are either nested dictionaries (for folders) or
      full S3 paths (for files).
    """
    try:
        # 1. List objects with the prefix (e.g., 'portfolio_data/')
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=f"{folder_name}/")

        if 'Contents' not in response:
            return {}, None

        file_tree: Dict[str, Any] = {}
        for obj in response['Contents']:
            key: str = obj['Key']

            # Skip empty directory markers
            if key.endswith('/'):
                continue

            # Split key: e.g., ['portfolio_data', 'run_id', 'filename']
            parts: List[str] = key.split('/')
            current_level: Dict[str, Any] = file_tree

            # We expect a structure that could be any depth.
            # Navigate/build the nested dictionary for the folders
            for part in parts[:-1]:
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]

            # Based on your structure, the last index is the file_name
            file_name: str = parts[-1]
            current_level[file_name] = key

        return file_tree, None
    except Exception as e:
        return None, str(e)


@validate_call
def get_metadata(local_path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Reads the TIF file header (using Rasterio), extracts bounds,
    and transforms them to EPSG:4326 (the web standard).
    """
    try:
        with rasterio.open(local_path) as src:
            # 1. Transform the bounds from the TIF's native CRS to EPSG:4326
            wgs84_bounds: tuple = transform_bounds(
                src_crs=src.crs,
                dst_crs='EPSG:4326',
                left=src.bounds.left,
                bottom=src.bounds.bottom,
                right=src.bounds.right,
                top=src.bounds.top
            )

            # 2. Package the reprojected bounds for the client
            return {
                       # We return the bounds in the required [W, S, E, N] format
                       "bounds": list(wgs84_bounds),
                       "crs": f'original: {src.crs.to_string()}, converted to EPSG:4326',
                       "file_type": "raster"
                   }, None

    except Exception as e:
        return None, str(e)


@validate_call
def process_tif_to_png(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Reads TIF, scales data (1-4 range to 0-255) ignoring NoData,
    and writes a temporary PNG.
    """
    if not os.path.exists(file_path):
        return None, "Raster source file not found."

    # Using your descriptive naming convention 
    temp_png_path: str = file_path + "_temp_output.png"

    try:
        with rasterio.open(file_path) as src:
            # 1. Read the array and get the NoData value
            image_array: np.ndarray = src.read(1)  # read first band of tif
            nodata_val = src.nodata

            # Exclude NoData from min/max calculations 
            if nodata_val is not None:
                valid_data = np.ma.masked_equal(image_array, nodata_val)
                min_val, max_val = np.min(valid_data), np.max(valid_data)
            else:
                min_val, max_val = np.min(image_array), np.max(image_array)

            # 2. Check the data range (should be 1 to 4)
            range_val = max_val - min_val
            if range_val <= 0:
                return None, "TIF data is uniform and cannot be scaled."

            # 3. Apply the scaling: (data - min) / (range) * 255
            # This scales the 1-4 range to 0-255
            image_array_scaled: np.ndarray = ((image_array - min_val) / range_val) * 255

            # 4. Convert to 8-bit integer type
            image_array_8bit: np.ndarray = image_array_scaled.astype(np.uint8)

            # 5. Re-apply the NoData mask to the 8-bit array (setting NoData pixels to 0)
            if nodata_val is not None:
                image_array_8bit[image_array == nodata_val] = 0

            out_profile: dict = src.profile
            out_profile.update(
                dtype=rasterio.uint8,
                count=1,  # Single band (Grayscale)
                driver='PNG',
                nodata=0)

            with rasterio.open(temp_png_path, 'w', **out_profile) as dst:
                dst.write(image_array_8bit, 1)

        return temp_png_path, None

    except Exception as e:
        return None, f"Error processing TIF: {e}"


@validate_call
def get_geojson_data(file_path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Reads the vector file (GPKG/SHP/GeoJSON), converts it to WGS84,
    and returns a dictionary for the Lambda response.
    """
    if not os.path.exists(file_path):
        return None, "Vector source file not found."

    try:
        # 1. Read the vector file into a GeoDataFrame
        gdf: gpd.GeoDataFrame = gpd.read_file(file_path)

        # 2. FORCE CONVERSION to WGS84 (EPSG:4326) 
        # This ensures the coordinates work with web map libraries
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs(epsg=4326)

        # 3. Convert to GeoJSON string and then back to a dictionary
        geojson_str: str = gdf.to_json()
        geo_data_dict: Dict[str, Any] = json.loads(geojson_str)

        return geo_data_dict, None

    except Exception as e:
        return None, f"Error processing vector file: {e}"


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # Set standard headers for CORS and JSON
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
    }

    # 1. Parse and Validate the request from API Gateway using Pydantic
    try:
        validated_event = ApiGatewayEvent(**event)
        params: Dict[str, str] = validated_event.pathParameters or {}
    except ValidationError as e:
        return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Malformed request structure"})}

    proxy_string: str = params.get('proxy', '')
    parts: List[str] = [p for p in proxy_string.split('/') if p]  # Clean up any empty strings

    # Ensure BUCKET_NAME exists
    if not BUCKET_NAME:
        return {"statusCode": 500, "headers": headers,
                "body": json.dumps({"error": "S3_BUCKET_NAME environment variable not set"})}

    # --- DYNAMIC ROUTE: get-file-structure (e.g., /api/get-file-structure/portfolio_data) ---
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "get-file-structure":
        # Join any remaining parts to support nested root folders if needed 
        folder_name: str = '/'.join(parts[2:])

        # We explicitly type these variables accepting the function output
        data: Optional[Dict[str, Any]]
        error: Optional[str]
        data, error = get_s3_file_structure(BUCKET_NAME, folder_name)

        if error:
            return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": error})}
        return {"statusCode": 200, "headers": headers, "body": json.dumps(data)}

    # --- EXISTING ROUTES: Metadata and Data Processing ---
    if len(parts) < 3:
        return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Invalid URL structure."})}

    file_name: str = parts[-1]
    # Join everything before the dynamic file path to get the 'command' path (e.g., 'api/metadata')
    command_path: str = '/'.join(parts[0:2])

    # Construct the S3 Key dynamically to handle any folder depth
    s3_key: str = '/'.join(parts[2:])

    # Define where to save it locally in the container
    # Replaces '/' with '_' so we don't try to write to non-existent subdirectories in /tmp/
    safe_filename: str = s3_key.replace('/', '_')
    local_path: str = f"/tmp/{safe_filename}"

    try:
        # 2. Download from S3 to /tmp
        s3.download_file(BUCKET_NAME, s3_key, local_path)

        # 3. Route to your existing processing functions

        # --- METADATA PATH ---
        if command_path == "api/metadata" and file_name.endswith(RASTER_FILES):
            metadata_dict: Optional[Dict[str, Any]]
            meta_error: Optional[str]
            metadata_dict, meta_error = get_metadata(local_path)
            os.remove(local_path)  # Clean up metadata source

            if meta_error:
                return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": meta_error})}

            return {"statusCode": 200, "headers": headers, "body": json.dumps(metadata_dict)}

        elif command_path == "api/get-data":

            # --- RASTER PATH ---
            if file_name.endswith(RASTER_FILES):
                png_path: Optional[str]
                raster_error: Optional[str]
                png_path, raster_error = process_tif_to_png(local_path)

                if raster_error:
                    if os.path.exists(local_path): os.remove(local_path)
                    return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": raster_error})}

                # 4. Convert PNG to Base64
                with open(png_path, "rb") as image_file:  # type: ignore
                    encoded_string: str = base64.b64encode(image_file.read()).decode('utf-8')

                # 5. Cleanup local files
                os.remove(local_path)
                os.remove(png_path)  # type: ignore

                return {
                    "statusCode": 200,
                    "headers": {**headers, "Content-Type": "image/png"},
                    "body": encoded_string,
                    "isBase64Encoded": True
                }

            # --- VECTOR PATH ---
            elif file_name.endswith(VECTOR_FILES):
                # Generate a secure, temporary URL directly to the S3 object
                try:
                    presigned_url = s3.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': BUCKET_NAME, 'Key': s3_key},
                        ExpiresIn=3600  # URL expires in 1 hour
                    )
                    
                    # Return just the URL, a tiny payload that will never hit the 6MB limit
                    response_payload = {"url": presigned_url}
                    
                    if os.path.exists(local_path): os.remove(local_path)
                    
                    return {"statusCode": 200, "headers": headers, "body": json.dumps(response_payload)}
                    
                except Exception as e:
                    if os.path.exists(local_path): os.remove(local_path)
                    return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": f"Failed to generate Presigned URL: {str(e)}"})}

        # Cleanup and error if no routes matched
        if os.path.exists(local_path): os.remove(local_path)
        return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "Unsupported route"})}

    except Exception as e:
        if os.path.exists(local_path): os.remove(local_path)
        return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": str(e)})}