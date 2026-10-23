"""Running the tests never reads or writes the user's real settings.

Loading the settings can set aside a damaged config.toml and write a repaired
one, and a TuxTalk installed on the same computer uses that file. conftest
points the settings and the data folder at a throwaway folder for every test;
a module that binds the settings path by name at import (as prefs does) keeps
the real one unless conftest knows about it too.
"""
import sys

from tuxtalk import config, prefs  # noqa: F401  (prefs binds the path by name)


def test_every_module_reads_the_settings_from_a_throwaway_folder(tmp_path_factory):
    throwaway = str(tmp_path_factory.getbasetemp())
    modules = {name: module for name, module in list(sys.modules.items())
               if name.startswith("tuxtalk")}
    paths = {name: module.CONFIG_PATH for name, module in modules.items()
             if isinstance(getattr(module, "CONFIG_PATH", None), str)}
    assert {"tuxtalk.config", "tuxtalk.prefs"} <= paths.keys()
    outside = {name: path for name, path in paths.items() if not path.startswith(throwaway)}
    assert outside == {}, f"these modules still have the real settings path: {outside}"


def test_the_data_folder_is_a_throwaway_one(tmp_path_factory):
    assert config.get_data_dir().startswith(str(tmp_path_factory.getbasetemp()))
    assert not config.get_data_dir().startswith(config._data_home())
