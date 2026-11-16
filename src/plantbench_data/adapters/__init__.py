"""Adapters are enabled only after source bytes and mappings are audited."""

REGISTRY = {
    "lettuce_rgb_env_biomass": "plantbench_data.adapters.lettuce:LettuceAdapter",
    "horti_m3_tomato": "plantbench_data.adapters.horti:HortiM3Adapter",
    "eschikon_stress": "plantbench_data.adapters.eschikon:EschikonAdapter",
    "tobrfv_lmid": "plantbench_data.adapters.tobrfv:ToBRFVAdapter",
    "mirage": "plantbench_data.adapters.mirage:MirageAdapter",
    "plantexpertvqa": None,
}


def get_adapter(dataset):
    from importlib import import_module
    from ..common import Blocked
    implementation = REGISTRY.get(dataset)
    if not implementation:
        raise Blocked(f"BLOCKED_ADAPTER_NOT_INTEGRATED:{dataset}")
    module, name = implementation.split(":")
    return getattr(import_module(module), name)
