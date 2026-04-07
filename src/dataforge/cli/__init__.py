# Note: app is imported directly in main.py to avoid circular imports
# Do not import app here as it creates a cycle: app → agents → exporter → preflight → cli.__init__
