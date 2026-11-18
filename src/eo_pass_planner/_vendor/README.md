# Vendored third-party code

`sgp4/` is the pure-Python part of [sgp4](https://pypi.org/project/sgp4/) 2.27
by Brandon Rhodes (MIT licence, see `sgp4/LICENSE`), copied unmodified.

It is used only when `sgp4` is not installed, so that the tool runs with just
numpy and PyYAML. An installed `sgp4` is preferred because it is faster.
