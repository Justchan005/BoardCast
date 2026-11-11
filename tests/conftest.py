"""Shared test settings. Loaded by pytest before any test module imports the backend."""
import os

# Tests never start the background pre-review agent on their own (it would call Gemini with a
# developer's real key). tests/test_prereview.py drives the agent directly with a scripted model.
os.environ["PREREVIEW_AUTO"] = "false"
