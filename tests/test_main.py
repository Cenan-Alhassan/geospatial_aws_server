import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import json
import base64

import numpy as np

# This line ensures Python can find your src/main.py file to test it
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import main

# --- CRITICAL FIX 1: Mock the Environment Variable ---
# This ensures the Lambda Handler doesn't instantly fail with a 500 error
main.BUCKET_NAME = "fake-test-bucket"

class TestGetS3FileStructure(unittest.TestCase):

    # @patch intercepts the global 's3' client inside main.py, specifically its list_objects_v2 method
    # This allows us to pass the mock_s3_list object to alter the method's behaviour   
    @patch('main.s3.list_objects_v2')
    def test_happy_path_nested_dict(self, mock_s3_list):
        """Test that a flat list of S3 keys is correctly converted into a nested dictionary."""
        
        # 1. ARRANGE: Tell the mock exactly what fake data to return
        # We are intercepting the list_objects_v2 method of the s3 client, so we return a 
        # structure that mimics what AWS would return for a bucket with nested folders and files.
        mock_s3_list.return_value = {
            'Contents': [
                {'Key': 'portfolio_data/project_a/map.geojson'},
                {'Key': 'portfolio_data/project_a/data.csv'},
                {'Key': 'portfolio_data/project_b/subfolder/image.tif'},
                {'Key': 'portfolio_data/empty_dir/'} # A folder marker (should be skipped)
            ]
        }

        # 2. ACT: Run our function
        result, error = main.get_s3_file_structure("fake-bucket", "portfolio_data")

        # 3. ASSERT: Verify the logic worked
        self.assertIsNone(error)

        self.assertIn("portfolio_data", result)
        portfolio_node = result["portfolio_data"]
        
        # Check Project A
        self.assertIn("project_a", portfolio_node)
        self.assertEqual(portfolio_node["project_a"]["map.geojson"], "portfolio_data/project_a/map.geojson")
        
        # Check Project B (Nested Subfolder)
        self.assertIn("subfolder", portfolio_node["project_b"])
        self.assertEqual(portfolio_node["project_b"]["subfolder"]["image.tif"], "portfolio_data/project_b/subfolder/image.tif")
        
        # Check that empty directory markers were properly ignored
        self.assertNotIn("empty_dir", portfolio_node)


    @patch('main.s3.list_objects_v2')
    def test_empty_folder(self, mock_s3_list):
        """Test how the function handles an S3 prefix that has no files in it."""
        
        # ARRANGE: An AWS response for an empty folder lacks the 'Contents' key
        mock_s3_list.return_value = {
            'IsTruncated': False, 
            'Name': 'fake-bucket', 
            'Prefix': 'empty_folder/'
        }

        # ACT
        result, error = main.get_s3_file_structure("fake-bucket", "empty_folder")

        # ASSERT: It should gracefully return an empty dictionary, not crash.
        self.assertIsNone(error)
        self.assertEqual(result, {})


    @patch('main.s3.list_objects_v2')
    def test_s3_exception(self, mock_s3_list):
        """Test that boto3 connection errors are caught and returned safely."""
        
        # ARRANGE: Tell the mock to raise a simulated crash
        mock_s3_list.side_effect = Exception("AWS Access Denied")

        # ACT
        result, error = main.get_s3_file_structure("fake-bucket", "secure_folder")

        # ASSERT: It should return None for data, and pass the string error message
        self.assertIsNone(result)
        self.assertEqual(error, "AWS Access Denied")


class TestGetGeojsonData(unittest.TestCase):

    @patch('main.os.path.exists')
    def test_file_not_found(self, mock_exists):
        """Test that missing files are caught before processing."""
        # ARRANGE: Pretend the file is not on the disk
        mock_exists.return_value = False

        # ACT
        result, error = main.get_geojson_data("/tmp/missing.geojson")

        # ASSERT
        self.assertIsNone(result)
        self.assertEqual(error, "Vector source file not found.")

    @patch('main.gpd.read_file')
    @patch('main.os.path.exists')
    # The order of the parameters in the test function is reversed from the order of the decorators.
    def test_successful_reprojection(self, mock_exists, mock_read_file):
        """Test that vector files in the wrong CRS are successfully converted to EPSG:4326."""
        # Must first pretend the file exists 
        mock_exists.return_value = True

        # ARRANGE: Create a fake GeoDataFrame object
        mock_gdf = MagicMock()
        # Pretend the file was uploaded in Web Mercator (EPSG:3857) instead of EPSG:4326
        mock_gdf.crs = "EPSG:3857" 
        
        # When .to_crs() is called, just return the mock object itself
        mock_gdf.to_crs.return_value = mock_gdf 
        # When .to_json() is called, return a valid JSON string
        mock_gdf.to_json.return_value = '{"type": "FeatureCollection"}' 
        
        mock_read_file.return_value = mock_gdf

        # ACT
        result, error = main.get_geojson_data("/tmp/valid.geojson")

        # ASSERT
        self.assertIsNone(error)
        self.assertEqual(result, {"type": "FeatureCollection"})
        # Verify our code actually attempted to force the EPSG:4326 conversion!
        mock_gdf.to_crs.assert_called_with(epsg=4326)


class TestProcessTifToPng(unittest.TestCase):

    @patch('main.os.path.exists')
    def test_file_not_found(self, mock_exists):
        """Test that missing rasters are caught early."""
        mock_exists.return_value = False

        result, error = main.process_tif_to_png("/tmp/missing.tif")

        self.assertIsNone(result)
        self.assertEqual(error, "Raster source file not found.")

    @patch('main.rasterio.open')
    @patch('main.os.path.exists') 
    def test_uniform_data_error(self, mock_exists, mock_rasterio_open):
        """Test that a completely uniform raster (max - min = 0) returns the safety error."""
        mock_exists.return_value = True

        # ARRANGE: Setup a fake rasterio source
        mock_src = MagicMock()
        
        # Create a tiny 2x2 grid where every pixel is exactly the value "2"
        mock_src.read.return_value = np.array([[2, 2], [2, 2]]) 
        mock_src.nodata = None
        
        # Because rasterio is opened with a 'with' statement, we must mock the __enter__ method
        mock_rasterio_open.return_value.__enter__.return_value = mock_src

        # ACT
        result, error = main.process_tif_to_png("/tmp/uniform.tif")

        # ASSERT
        self.assertIsNone(result)
        self.assertEqual(error, "TIF data is uniform and cannot be scaled.")

class TestLambdaHandler(unittest.TestCase):

    @patch('main.get_s3_file_structure')
    def test_file_structure_route(self, mock_get_structure):
        """Test that the handler correctly routes a request for the folder structure."""
        # ARRANGE: Tell our helper function mock to return a success response
        mock_get_structure.return_value = ({"portfolio_data": {"project": "file.shp"}}, None)

        # Create a fake AWS API Gateway event
        event = {
            "pathParameters": {
                "proxy": "api/get-file-structure/portfolio_data"
            }
        }

        # ACT
        response = main.lambda_handler(event, None)

        # ASSERT: Check that it returned a 200 OK and the correct JSON body
        self.assertEqual(response["statusCode"], 200)
        
        body = json.loads(response["body"])
        self.assertIn("portfolio_data", body)
        # Verify that the get_s3_file_structure function was called with the correct parameters
        mock_get_structure.assert_called_once_with(main.BUCKET_NAME, "portfolio_data")


    def test_malformed_url(self):
        """Test that short or broken URLs are rejected with a 400 Bad Request."""
        # ARRANGE: A proxy string that is too short (only 2 parts instead of 3+)
        event = {
            "pathParameters": {
                "proxy": "api/get-data" 
            }
        }

        # ACT
        response = main.lambda_handler(event, None)

        # ASSERT
        self.assertEqual(response["statusCode"], 400)
        self.assertIn("Invalid URL structure", response["body"])


    @patch('main.s3.download_file')
    @patch('main.os.path.exists')
    @patch('main.os.remove')
    def test_unsupported_extension(self, mock_remove, mock_exists, mock_download):
        """Test that requesting an invalid file type (like .pdf) is rejected and cleaned up."""
        # ARRANGE: Pretend the download worked, and the file exists in /tmp/
        mock_exists.return_value = True

        event = {
            "pathParameters": {
                "proxy": "api/get-data/portfolio/document.pdf"
            }
        }

        # ACT
        response = main.lambda_handler(event, None)

        # ASSERT: Should return 404 Not Found
        self.assertEqual(response["statusCode"], 404)
        self.assertIn("Unsupported extension", response["body"])
        
        # CRITICAL: Verify that the os.remove function removed the temporary file
        mock_remove.assert_called_once_with("/tmp/portfolio_document.pdf")


    @patch('main.s3.download_file')
    @patch('main.get_geojson_data')
    @patch('main.os.path.exists')
    @patch('main.os.remove')
    def test_backend_crash_cleanup(self, mock_remove, mock_exists, mock_get_geojson, mock_download):
        """Test that if a processing function crashes, the server returns 500 and cleans up."""
        mock_exists.return_value = True
        
        # ARRANGE: Force the helper function to crash
        mock_get_geojson.side_effect = Exception("A catastrophic failure occurred")

        event = {
            "pathParameters": {
                "proxy": "api/get-data/portfolio/map.geojson"
            }
        }

        # ACT
        response = main.lambda_handler(event, None)

        # ASSERT: Should catch the error, return 500, and NOT crash the whole container
        self.assertEqual(response["statusCode"], 500)
        self.assertIn("catastrophic failure", response["body"])
        
        # CRITICAL: Verify it still deleted the temporary file despite the crash
        mock_remove.assert_called_once_with("/tmp/portfolio_map.geojson")

    @patch('main.s3.download_file')
    @patch('main.get_geojson_data')
    @patch('main.os.path.exists')
    @patch('main.os.remove')
    def test_vector_happy_path(self, mock_remove, mock_exists, mock_get_geojson, mock_download):
        """Test that a valid vector request routes correctly and returns a 200 OK with JSON."""
        # ARRANGE: Simulate successful S3 download and vector processing
        mock_exists.return_value = True
        mock_get_geojson.return_value = ({"type": "FeatureCollection"}, None)

        event = {
            "pathParameters": {
                "proxy": "api/get-data/portfolio/project_a/map.geojson"
            }
        }

        # ACT
        response = main.lambda_handler(event, None)

        # ASSERT: Check HTTP packaging
        self.assertEqual(response["statusCode"], 200)
        
        # Verify the body is the JSON we expect
        body = json.loads(response["body"])
        self.assertEqual(body, {"type": "FeatureCollection"})
        
        # Verify it cleaned up the temporary S3 download
        mock_remove.assert_called_once_with("/tmp/portfolio_project_a_map.geojson")


    @patch('main.s3.download_file')
    @patch('main.process_tif_to_png')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data=b'fake_image_bytes')
    @patch('main.os.path.exists')
    @patch('main.os.remove')
    def test_raster_happy_path(self, mock_remove, mock_exists, mock_file_open, mock_process_tif, mock_download):
        """Test that a valid raster request encodes to Base64 and returns a 200 OK image response."""
        # ARRANGE: Simulate successful processing returning a temporary PNG path
        mock_exists.return_value = True
        mock_process_tif.return_value = ("/tmp/portfolio_project_a_map.tif_temp_output.png", None)

        event = {
            "pathParameters": {
                "proxy": "api/get-data/portfolio/project_a/map.tif"
            }
        }

        # ACT
        response = main.lambda_handler(event, None)

        # ASSERT: Check HTTP packaging for binary data
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["Content-Type"], "image/png")
        self.assertTrue(response["isBase64Encoded"])
        
        # Verify the body contains the Base64 representation of our b'fake_image_bytes'
        expected_base64 = base64.b64encode(b'fake_image_bytes').decode('utf-8')
        self.assertEqual(response["body"], expected_base64)

        # Verify it cleaned up BOTH the downloaded TIF and the temporary PNG
        mock_remove.assert_any_call("/tmp/portfolio_project_a_map.tif")
        mock_remove.assert_any_call("/tmp/portfolio_project_a_map.tif_temp_output.png")

if __name__ == '__main__':
    unittest.main()