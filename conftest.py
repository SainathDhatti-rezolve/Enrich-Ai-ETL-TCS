"""Root conftest.

Its presence at the project root ensures pytest adds this directory to
sys.path, so tests can `import src.transform` etc. without extra config.
"""
