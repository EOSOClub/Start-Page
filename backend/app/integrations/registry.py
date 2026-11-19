"""Discovers the type modules in `integrations/types/` and holds the registry."""
import importlib
import pkgutil
from types import ModuleType

from . import types as types_package
from .base import Spec


def discover(package: ModuleType) -> dict[str, Spec]:
    """Import every module of `package` and collect its `SPEC`. A module without a Spec or a
    repeated type key raises RuntimeError, so a mistake stops the app at import time."""
    specs: dict[str, Spec] = {}
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        spec = getattr(module, "SPEC", None)
        if not isinstance(spec, Spec):
            raise RuntimeError(f"{module.__name__} must define SPEC = Spec(...)")
        if spec.type in specs:
            raise RuntimeError(f"integration type {spec.type!r} is defined twice (in {module.__name__} and earlier)")
        specs[spec.type] = spec
    return specs


SPECS: dict[str, Spec] = discover(types_package)
TYPES: list[str] = sorted(SPECS)
