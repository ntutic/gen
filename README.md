# gen

Project generators. Each subdirectory containing a `generate-project.py` is
one generator:

```bash
mkdir vclist && cd vclist
gen project-scraping vclist --records company
```

`gen --help` lists available generators. A generator writes a ready project
into the current directory; see its own README for setup. Put the `gen`
executable on your `PATH` (e.g. symlink it into `~/.local/bin`) to run it
from anywhere.
