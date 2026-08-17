# Empty — these engines have no config files

`deep_15m_optimizer.py` and `multi_tf_optimizer.py` are standalone `__main__` scripts with
every setting hardcoded as a module-level constant: no CLI arguments, no config file, no
symbol/date/trial overrides. `new_optimizer_v2` likewise carries its settings in code; its
selection rule is documented in `../source/new_optimizer_v2_package/SELECTION_RULE.md`.
