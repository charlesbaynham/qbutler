"""Dashboard applets that visualise a running qbutler calibration tree.

These modules are run as standalone applet processes by the ARTIQ dashboard
(``${python} -m qbutler.applets.<name> ...``) and are launched automatically
via the CCB (see :mod:`qbutler.ccb`). They depend on ``pyqtgraph`` and the
ARTIQ dashboard machinery, which are only present in the dashboard's
environment; for that reason this package is never imported by
``qbutler`` itself — importing it is opt-in and happens only inside the applet
process.
"""
