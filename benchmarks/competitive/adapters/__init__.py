"""Adapters of the competitive tier: one module per tool, `make()` returns it.

purpose:  the package the runner imports adapters from, by name (adapter.REGISTRY)
invokes:  nothing
produces: nothing
refuses:  nothing
pinned:   n/a
fairness: each module carries its own header; docs/competitive/DESIGN.md s1
"""
