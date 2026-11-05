# PyPad

A small, fast, Python-focused text editor built with PyQt6. It also works great for plain `.txt` files.

## Install (Debian / Ubuntu)

Download the latest `.deb` from the [Releases](../../releases) page, then:

```bash
sudo apt install ./pypad.deb
```

Using `apt install ./...` also installs the dependency (`python3-pyqt6`) automatically.

Run it:

```bash
pypad              # open PyPad
pypad here.txt     # open here.txt (created on save if it doesn't exist)
```

Uninstall:

```bash
sudo apt remove pypad
```

## Run from source

Requires Python 3.8+ and PyQt6.

```bash
pip install PyQt6
python3 main.py
python3 main.py notes.txt
```

## Keyboard shortcuts

| Action | Shortcut |
| --- | --- |
| New | `Ctrl+N` |
| Open | `Ctrl+O` |
| Save | `Ctrl+S` |
| Save As | `Ctrl+Shift+S` |
| Duplicate tab | `Ctrl+Shift+D` |
| Close all tabs | `Ctrl+Shift+W` |
| Quit | `Ctrl+Q` |
| Increase font | `Ctrl++` |
| Decrease font | `Ctrl+-` |
| Toggle theme | `Ctrl+T` |
| Go to line | `Ctrl+G` |
| Find and replace | `Ctrl+F` |
| Settings (JSON) | `Ctrl+,` |

## Settings

Open **Edit > Settings (JSON)** to edit the settings file. Changes apply as soon as you save it.

Location on Linux: `~/.config/pypad/settings.json`

```json
{
  "font_size": 12,
  "tab_width": 4,
  "dark_mode": false,
  "recent_files": []
}
```

| Key | Description | Range |
| --- | --- | --- |
| `font_size` | Editor font size | 6 - 48 |
| `tab_width` | Spaces per Tab | 1 - 16 |
| `dark_mode` | Use the dark theme | `true` / `false` |
| `recent_files` | Managed by the app | list of paths |

Open tabs (including unsaved content) are saved on quit to `~/.config/pypad/session.json` and restored on the next start.
