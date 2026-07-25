import sys
import os
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

# Add the parent directory (api-handler) to sys.path so tests can import auth, db, index, and routes
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
