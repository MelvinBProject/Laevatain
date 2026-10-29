import tempfile
from pathlib import Path


def pytest_configure(config):
    if config.option.basetemp:
        return
    root = Path(__file__).resolve().parents[1] / 'data' / 'test-runs'
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='run-', dir=root)).resolve()
    if not folder.is_relative_to(root.resolve()):
        raise RuntimeError('La carpeta de pruebas no es valida')
    config.option.basetemp = str(folder)
