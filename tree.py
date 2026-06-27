from pathlib import Path

def tree(path, prefix=""):
    items = sorted(path.iterdir())
    items = [i for i in items if i.name != ".venv"]

    for index, item in enumerate(items):
        connector = "└── " if index == len(items)-1 else "├── "
        print(prefix + connector + item.name)

        if item.is_dir():
            extension = "    " if index == len(items)-1 else "│   "
            tree(item, prefix + extension)

tree(Path("."))