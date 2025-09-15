#!/usr/bin/env python3
"""Simple HTTP server to serve the point cloud viewer"""
import http.server
import socketserver
import os
import webbrowser

PORT = 8080

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Add CORS headers to allow API calls
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
    print(f"Server running at http://localhost:{PORT}/")
    print(f"Point Cloud Viewer at: http://localhost:{PORT}/point_cloud_viewer.html")
    print("Press Ctrl+C to stop")
    
    # Open browser automatically
    webbrowser.open(f'http://localhost:{PORT}/point_cloud_viewer.html')
    
    httpd.serve_forever()