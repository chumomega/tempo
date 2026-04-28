# Empty conftest at the project root: pytest treats its parent dir as the
# rootdir and adds it to sys.path, so `pytest tests/` can import the `tempo`
# package without invoking pytest as a module.
