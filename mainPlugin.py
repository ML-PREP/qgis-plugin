import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt

from .app.main_window import UnifiedPluginDialog


class MainPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dlg = None

    def initGui(self):
        icon_path = os.path.join(
            self.plugin_dir, "mlprep-icon.jpg"
        )  # Or whichever icon you prefer
        self.action = QAction(
            QIcon(icon_path), "MLPREP Detection Suite", self.iface.mainWindow()
        )
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&MLPREP Suite", self.action)

    def unload(self):
        self.iface.removePluginMenu("&MLPREP Suite", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        def none_ref():
            self.dlg = None

        if self.dlg is None:
            self.dlg = UnifiedPluginDialog(self.iface.mainWindow())
            self.dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            self.dlg.destroyed.connect(none_ref)
        self.dlg.showMaximized()
        self.dlg.raise_()
        self.dlg.activateWindow()
