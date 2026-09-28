# Darcy Iowa Geothermal Suitability Map

Interactive map demos, source datasets, processing scripts, and Google Earth Engine proxy code.

## Continue on another computer

Install Git, [Git LFS](https://git-lfs.com/), and Python (the existing environment used Python 3.9.6). Authenticate with GitHub using an account with access to this private repository.

```sh
git lfs install
git clone https://github.com/qtyree1328/Darcy_suitabilityMap_demo.git
cd Darcy_suitabilityMap_demo
git lfs pull
python3 -m http.server 8080
```

Open http://localhost:8080/darcy_v3.html for V3. Earlier demos are available at `/index.html` and `/darcy_v2.html`. The included web assets let you view the demos without rebuilding the datasets; external basemap services still require internet access.

## Python data processing

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate`. Dependency versions reflect the original environment; newer Python versions may need updated package versions.

- `scripts/`: original and V2 data-fetching, Earth Engine, and export scripts.
- `scripts_v3/`: V3 scoring, model building, and export scripts, numbered in execution order.
- `data/raw/`, `data/processed/`: source and intermediate datasets.
- `data/tiles/`, `data/tiles_v2/`, `data/tiles_v3/`: ready-to-use web assets.
- `DARCY_IOWA_SPRINT.md`, `Iowa_Geothermal_V3_Plan.md`, and `potential_modifications.md`: design and development notes.

The two largest processed GeoJSON files use Git LFS. Run `git lfs pull` if they appear as small text pointers after cloning.

## Optional Earth Engine proxy

`server/gee-proxy.js` requires Node.js with native `fetch` support and the `google-auth-library` package. It uses ES module syntax. Configure a Node environment with `"type": "module"`, install `google-auth-library`, and run `node server/gee-proxy.js`. The default port is 3001; `PORT` overrides it.

Set `GEE_SERVICE_ACCOUNT_PATH` to your separately provisioned service-account JSON, or configure Google Application Default Credentials. Earth Engine workflows require access to the Google project and assets referenced by the scripts. See `GEE-PROXY.md` for the API details.

Virtual environments, installed dependencies, credentials, and OS cache files are excluded from Git.
