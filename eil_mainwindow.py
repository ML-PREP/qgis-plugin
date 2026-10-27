# -*- coding: utf-8 -*-

# qgis.PyQt maps to whichever Qt binding this QGIS was built against.
# Importing PyQt5 directly breaks on a Qt6 QGIS at plugin-load time.
from qgis.PyQt import QtCore, QtGui, QtWidgets

from .common.responsive_columns import (
    ResponsiveColumns,
    compact_combo,
    no_horizontal_scroll,
)
from .log_panel import LogPanel

# Same section look as the Landslide Detection tab: a 2px rounded border with
# the title centred on the top edge. The title background must match the panel
# behind it (white) so it cuts the border line cleanly.
PANEL_STYLE = (
    'QGroupBox { font: 600 12px "Arial"; color: black; border: 2px solid #555;'
    " border-radius: 10px; margin-top: 16px; padding: 12px;"
    " background-color: transparent; }"
    "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center;"
    " padding: 0 8px; background-color: white; color: black; margin-top: 10px; }"
)

# Untitled panels (chart, log console) get the same border as the group boxes.
# Scoped by object name: a bare `QFrame` rule would also hit QLabel and
# QTableWidget, which are QFrame subclasses.
FRAME_STYLE = (
    "QFrame#{name} {{ background-color: white; border: 2px solid #555;"
    " border-radius: 10px; }}"
)

# Field + "..." button rendered as one joined control, mirroring the Landslide
# tab's button + path pairs (there the button sits on the left).
JOINED_PICKER_STYLE = (
    "QComboBox, QPlainTextEdit { border-top-right-radius: 0;"
    " border-bottom-right-radius: 0; }"
    "QPushButton { border-left: 0; border-top-left-radius: 0;"
    " border-bottom-left-radius: 0; }"
    "QLabel { padding-right: 10px; }"
)

HAZARD_TABLE_HEADERS = ("Municipality", "High", "Moderate", "Low")
TABLE_ROW_HEIGHT = 30
HAZARD_TABLE_STYLE = (
    "QTableWidget { background-color: white; alternate-background-color: #f6f7f9;"
    " color: #1f2328; border: 1px solid #d8dbe0; border-radius: 6px;"
    " font-size: 12px; }"
    "QTableWidget::item { padding: 0 6px; border: none; }"
    "QHeaderView { background-color: transparent; }"
    "QHeaderView::section:horizontal { background-color: #eef0f3; color: #1f2328;"
    " font-size: 11px; font-weight: 600; border: none;"
    " border-bottom: 1px solid #d8dbe0; padding: 6px; }"
    "QHeaderView::section:vertical { background-color: white; color: #8a9099;"
    " font-size: 11px; border: none; padding: 0 4px; }"
    "QTableCornerButton::section { background-color: #eef0f3; border: none;"
    " border-bottom: 1px solid #d8dbe0; }"
)
PANEL_TITLE_STYLE = 'font: 600 13px "Arial"; color: #1f2328;'
PANEL_CAPTION_STYLE = "font-size: 11px; color: #6b7280;"
EMPTY_STATE_STYLE = (
    "QLabel { color: #6b7280; font-size: 12px; font-style: italic; padding: 20px 12px;"
    " border: 1px dashed #c8ccd2; border-radius: 6px; }"
)


def _results_panel(parent, name):
    """(frame, layout) of a bordered results panel."""
    frame = QtWidgets.QFrame(parent)
    frame.setObjectName(name)
    frame.setStyleSheet(FRAME_STYLE.format(name=name))
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(8)
    return frame, layout


def _panel_heading(frame, layout):
    """(title, caption) labels at the top of a results panel; both wrap."""
    heading = QtWidgets.QVBoxLayout()
    heading.setSpacing(2)
    labels = []
    for style in (PANEL_TITLE_STYLE, PANEL_CAPTION_STYLE):
        label = QtWidgets.QLabel(frame)
        label.setWordWrap(True)
        label.setStyleSheet(style)
        heading.addWidget(label)
        labels.append(label)
    layout.addLayout(heading)
    return tuple(labels)


def _empty_state(parent):
    label = QtWidgets.QLabel(parent)
    label.setObjectName("label_HazardEmpty")
    label.setAlignment(QtCore.Qt.AlignCenter)
    label.setWordWrap(True)
    label.setStyleSheet(EMPTY_STATE_STYLE)
    return label


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(990, 950)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setStyleSheet(
            "QGroupBox::title {\n"
            "    background-color: transparent;\n"
            "    padding-left: 5px;\n"
            "    padding-right: 5px;\n"
            "}\n"
            "QPlainTextEdit { border-radius: 4px; border: 1px solid #b3b3b3; }\n"
            "QLabel, QCheckBox { background-color: transparent; }\n"
            "QGroupBox::indicator { width: 14px; height: 14px; border: 1px solid #b3b3b3; border-radius: 3px; background-color: white; }\n"
            "QGroupBox::indicator:checked { background-color: #007aff; border: 1px solid #007aff; }\n"
            "QComboBox {\n"
            "    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e6e6e6);\n"
            "    border: 1px solid #b3b3b3; border-radius: 4px; padding: 3px 10px; color: black; min-height: 22px;\n"
            "}\n"
            "QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 25px; border-left: 1px solid #cccccc; border-top-right-radius: 4px; border-bottom-right-radius: 4px; }\n"
            "QComboBox::down-arrow { image: url(:/down-arrow.png); width: 12px; height: 12px; }\n"
            "QComboBox::down-arrow:on { top: 1px; left: 1px; }\n"
            "QPushButton {\n"
            "    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e6e6e6);\n"
            "    border: 1px solid #b3b3b3; border-radius: 4px; padding: 3px 10px; color: black; min-height: 22px;\n"
            "}\n"
            "QPushButton:pressed { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e6e6e6, stop:1 #d4d4d4); }\n"
            "QProgressBar { border: 1px solid #b3b3b3; border-radius: 4px; background-color: #f0f0f0; text-align: center; color: black; min-height: 18px; max-height: 18px; }\n"
            "QProgressBar::chunk { background-color: #007aff; border-radius: 3px; }"
        )
        self.centralwidget.setObjectName("centralwidget")
        self.verticalLayout_3 = QtWidgets.QVBoxLayout(self.centralwidget)
        self.verticalLayout_3.setSpacing(0)
        self.verticalLayout_3.setObjectName("verticalLayout_3")

        self.scrollAreaLeft = QtWidgets.QScrollArea(self.centralwidget)
        self.scrollAreaLeft.setStyleSheet('background-color:"white";\ncolor:"black";')
        self.scrollAreaLeft.setWidgetResizable(True)
        self.scrollAreaLeft.setObjectName("scrollAreaLeft")
        self.scrollAreaWidgetContentsLeft = QtWidgets.QWidget()
        self.scrollAreaWidgetContentsLeft.setGeometry(QtCore.QRect(0, 0, 705, 1100))
        self.scrollAreaWidgetContentsLeft.setObjectName("scrollAreaWidgetContentsLeft")
        # Set on the container so the ROI box RoiSelector inserts later
        # inherits it too.
        self.scrollAreaWidgetContentsLeft.setStyleSheet(PANEL_STYLE)
        self.verticalLayout = QtWidgets.QVBoxLayout(self.scrollAreaWidgetContentsLeft)
        self.verticalLayout.setObjectName("verticalLayout")
        self.verticalLayout.setSpacing(12)

        # ==========================================
        # 🟢 PARAMETER GRID
        # ==========================================
        self.groupBox_Parameters = QtWidgets.QGroupBox(
            self.scrollAreaWidgetContentsLeft
        )
        self.groupBox_Parameters.setObjectName("groupBox_Parameters")
        self.groupBox_Parameters.setStyleSheet(JOINED_PICKER_STYLE)
        self.gridLayout = QtWidgets.QGridLayout(self.groupBox_Parameters)
        self.gridLayout.setObjectName("gridLayout")
        self.gridLayout.setHorizontalSpacing(0)

        sizePolicyCombo = QtWidgets.QSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        sizePolicyCombo.setHorizontalStretch(0)
        sizePolicyCombo.setVerticalStretch(0)

        # DEM ROW
        self.label_DEM = QtWidgets.QLabel(self.groupBox_Parameters)
        self.label_DEM.setObjectName("label_DEM")
        self.gridLayout.addWidget(self.label_DEM, 0, 0, 1, 1)

        self.comboBox_DEM = compact_combo(QtWidgets.QComboBox(self.groupBox_Parameters))
        self.comboBox_DEM.setSizePolicy(sizePolicyCombo)
        self.comboBox_DEM.setObjectName("comboBox_DEM")
        self.gridLayout.addWidget(self.comboBox_DEM, 0, 1, 1, 1)

        self.pushButton_DEM = QtWidgets.QPushButton(self.groupBox_Parameters)
        self.pushButton_DEM.setMaximumWidth(40)
        self.pushButton_DEM.setObjectName("pushButton_DEM")
        self.gridLayout.addWidget(self.pushButton_DEM, 0, 2, 1, 1)

        parameters = [
            ("BulkDensity", "Bulk Density"),
            ("Clay", "Clay"),
            ("Sand", "Sand"),
            ("Silt", "Silt"),
            ("Slope", "Slope"),
            ("PGA", "Peak Ground Acceleration (PGA)"),
            ("PRC", "Precipitation"),
            ("SoilThickness", "Soil Thickness"),
            ("SoilType", "Soil Type / Lithology"),
            ("ContributingFactor", "Contributing Factor"),
            # Read only by the depositional zone's ML-DFI model, not the PINN.
            ("NDVI", "NDVI (depositional zone)"),
        ]

        for idx, (name, title) in enumerate(parameters):
            row = idx + 1
            label = QtWidgets.QLabel(self.groupBox_Parameters)
            label.setObjectName(f"label_{name}")
            # "Peak Ground Acceleration (PGA)" may wrap on a narrow column.
            label.setWordWrap(True)
            self.gridLayout.addWidget(label, row, 0, 1, 1)
            setattr(self, f"label_{name}", label)

            combo = compact_combo(QtWidgets.QComboBox(self.groupBox_Parameters))
            combo.setSizePolicy(sizePolicyCombo)
            combo.setObjectName(f"comboBox_{name}")
            self.gridLayout.addWidget(combo, row, 1, 1, 1)
            setattr(self, f"comboBox_{name}", combo)

            btn = QtWidgets.QPushButton(self.groupBox_Parameters)
            btn.setMaximumWidth(40)
            btn.setObjectName(f"pushButton_{name}")
            self.gridLayout.addWidget(btn, row, 2, 1, 1)
            setattr(self, f"pushButton_{name}", btn)

        self.gridLayout.setColumnStretch(1, 5)
        self.verticalLayout.addWidget(self.groupBox_Parameters)

        # ==========================================
        # 🟢 UPDATED: SLOPE UNIT (Max Iteration Only)
        # ==========================================
        self.groupBox_SlopeUnit = QtWidgets.QGroupBox(self.scrollAreaWidgetContentsLeft)
        self.groupBox_SlopeUnit.setStyleSheet(
            "QLabel { background-color: transparent; }"
        )
        self.groupBox_SlopeUnit.setObjectName("groupBox_SlopeUnit")
        self.gridLayout_2 = QtWidgets.QGridLayout(self.groupBox_SlopeUnit)
        self.gridLayout_2.setContentsMargins(-1, 10, -1, 12)

        self.label_MaxIteration = QtWidgets.QLabel(self.groupBox_SlopeUnit)
        self.label_MaxIteration.setObjectName("label_MaxIteration")
        self.gridLayout_2.addWidget(self.label_MaxIteration, 0, 0, 1, 1)

        self.plainTextEdit_MaxIteration = QtWidgets.QPlainTextEdit(
            self.groupBox_SlopeUnit
        )
        self.plainTextEdit_MaxIteration.setMaximumSize(QtCore.QSize(16777215, 30))
        self.plainTextEdit_MaxIteration.setObjectName("plainTextEdit_MaxIteration")
        self.gridLayout_2.addWidget(self.plainTextEdit_MaxIteration, 0, 1, 1, 1)

        self.verticalLayout.addWidget(self.groupBox_SlopeUnit)

        # ==========================================
        # PINN MODEL (listed in model/pinn_models.json)
        # ==========================================
        self.groupBox_Model = QtWidgets.QGroupBox(self.scrollAreaWidgetContentsLeft)
        self.groupBox_Model.setObjectName("groupBox_Model")
        self.layout_Model = QtWidgets.QVBoxLayout(self.groupBox_Model)
        self.layout_Model.setContentsMargins(-1, 10, -1, 12)

        self.comboBox_Model = compact_combo(QtWidgets.QComboBox(self.groupBox_Model))
        self.comboBox_Model.setSizePolicy(sizePolicyCombo)
        self.comboBox_Model.setObjectName("comboBox_Model")
        self.layout_Model.addWidget(self.comboBox_Model)

        self.label_ModelDescription = QtWidgets.QLabel(self.groupBox_Model)
        self.label_ModelDescription.setObjectName("label_ModelDescription")
        self.layout_Model.addWidget(self.label_ModelDescription)

        self.verticalLayout.addWidget(self.groupBox_Model)

        # ==========================================
        # 🟢 UPDATED: SINGLE OUTPUT OPTIONS
        # ==========================================
        self.groupBox_Option = QtWidgets.QGroupBox(self.scrollAreaWidgetContentsLeft)
        self.groupBox_Option.setStyleSheet(JOINED_PICKER_STYLE + """
            QLabel { background-color: transparent; }
            QCheckBox { background-color: transparent; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #b3b3b3; border-radius: 3px; background-color: white; }
            QCheckBox::indicator:hover { border: 1px solid #007aff; }
            QCheckBox::indicator:checked { background-color: #007aff; border: 1px solid #007aff; }
        """)
        self.groupBox_Option.setObjectName("groupBox_Option")
        self.gridLayout_5 = QtWidgets.QGridLayout(self.groupBox_Option)
        self.gridLayout_5.setVerticalSpacing(12)
        self.gridLayout_5.setHorizontalSpacing(0)

        self.label_OutputFolder = QtWidgets.QLabel(self.groupBox_Option)
        self.label_OutputFolder.setObjectName("label_OutputFolder")
        self.gridLayout_5.addWidget(self.label_OutputFolder, 0, 0, 1, 2)

        self.plainTextEdit_OutputFolder = QtWidgets.QPlainTextEdit(self.groupBox_Option)
        self.plainTextEdit_OutputFolder.setMaximumSize(QtCore.QSize(16777215, 30))
        self.plainTextEdit_OutputFolder.setObjectName("plainTextEdit_OutputFolder")
        self.gridLayout_5.addWidget(self.plainTextEdit_OutputFolder, 1, 0, 1, 1)

        self.pushButton_OutputFolder = QtWidgets.QPushButton(self.groupBox_Option)
        self.pushButton_OutputFolder.setMaximumWidth(40)
        self.pushButton_OutputFolder.setObjectName("pushButton_OutputFolder")
        self.gridLayout_5.addWidget(self.pushButton_OutputFolder, 1, 1, 1, 1)

        self.checkBox_LoadResults = QtWidgets.QCheckBox(self.groupBox_Option)
        self.checkBox_LoadResults.setObjectName("checkBox_LoadResults")
        self.checkBox_LoadResults.setChecked(True)  # Checked by default
        self.gridLayout_5.addWidget(self.checkBox_LoadResults, 2, 0, 1, 2)

        self.verticalLayout.addWidget(self.groupBox_Option)

        spacerItem = QtWidgets.QSpacerItem(
            20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding
        )
        self.verticalLayout.addItem(spacerItem)
        self.scrollAreaLeft.setWidget(self.scrollAreaWidgetContentsLeft)

        # ==========================================
        # MIDDLE COLUMN (Table & Chart)
        # ==========================================
        self.scrollAreaMiddle = QtWidgets.QScrollArea(self.centralwidget)
        self.scrollAreaMiddle.setStyleSheet("background-color: white; color: black;")
        self.scrollAreaMiddle.setWidgetResizable(True)
        self.scrollAreaMiddle.setObjectName("scrollAreaMiddle")

        self.scrollAreaWidgetContentsMiddle = QtWidgets.QWidget()
        self.scrollAreaWidgetContentsMiddle.setObjectName(
            "scrollAreaWidgetContentsMiddle"
        )

        self.verticalLayout_Middle = QtWidgets.QVBoxLayout(
            self.scrollAreaWidgetContentsMiddle
        )
        self.verticalLayout_Middle.setObjectName("verticalLayout_Middle")
        self.verticalLayout_Middle.setSpacing(12)
        self.scrollAreaWidgetContentsMiddle.setStyleSheet(PANEL_STYLE)

        # Results panels: a wrapping title + caption instead of a group-box
        # title, which cannot wrap and was clipped in a narrow column.
        self.frame_Middle1, self.layout_Middle1 = _results_panel(
            self.scrollAreaWidgetContentsMiddle, "frame_Middle1"
        )
        self.label_TableTitle, self.label_TableCaption = _panel_heading(
            self.frame_Middle1, self.layout_Middle1
        )

        self.tableWidget_Hazard = QtWidgets.QTableWidget(self.frame_Middle1)
        self.tableWidget_Hazard.setObjectName("tableWidget_Hazard")
        self.tableWidget_Hazard.setColumnCount(len(HAZARD_TABLE_HEADERS))
        for column, text in enumerate(HAZARD_TABLE_HEADERS):
            item = QtWidgets.QTableWidgetItem(text)
            # Names read left to right; counts line up on their last digit.
            horizontal = QtCore.Qt.AlignRight if column else QtCore.Qt.AlignLeft
            item.setTextAlignment(int(horizontal | QtCore.Qt.AlignVCenter))
            self.tableWidget_Hazard.setHorizontalHeaderItem(column, item)
        self.tableWidget_Hazard.setStyleSheet(HAZARD_TABLE_STYLE)
        self.tableWidget_Hazard.setAlternatingRowColors(True)
        self.tableWidget_Hazard.setShowGrid(False)
        self.tableWidget_Hazard.setWordWrap(False)
        self.tableWidget_Hazard.setTextElideMode(QtCore.Qt.ElideRight)
        self.tableWidget_Hazard.setFocusPolicy(QtCore.Qt.NoFocus)
        self.tableWidget_Hazard.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection
        )
        self.tableWidget_Hazard.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        # Every row is shown (the table sizes itself to them), so it never
        # scrolls; long names elide and show in full as a tooltip.
        self.tableWidget_Hazard.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.tableWidget_Hazard.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.tableWidget_Hazard.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        # Rank numbers down the side.
        ranks = self.tableWidget_Hazard.verticalHeader()
        ranks.setDefaultSectionSize(TABLE_ROW_HEIGHT)
        ranks.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        ranks.setDefaultAlignment(QtCore.Qt.AlignCenter)
        # The name takes whatever the counts leave; counts fit their digits.
        header = self.tableWidget_Hazard.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setHighlightSections(False)
        self.tableWidget_Hazard.hide()  # shown once there are results
        self.layout_Middle1.addWidget(self.tableWidget_Hazard)

        self.label_HazardEmpty = _empty_state(self.frame_Middle1)
        self.layout_Middle1.addWidget(self.label_HazardEmpty)
        self.verticalLayout_Middle.addWidget(self.frame_Middle1)

        self.frame_Middle2, self.layout_Middle2 = _results_panel(
            self.scrollAreaWidgetContentsMiddle, "frame_Middle2"
        )
        self.label_ChartTitle, self.label_ChartCaption = _panel_heading(
            self.frame_Middle2, self.layout_Middle2
        )
        self.verticalLayout_Middle.addWidget(self.frame_Middle2)

        # Both panels are as tall as their rows; spare height stays below.
        self.verticalLayout_Middle.addStretch(1)
        self.scrollAreaMiddle.setWidget(self.scrollAreaWidgetContentsMiddle)

        # ==========================================
        # RIGHT COLUMN (Logs & Details)
        # ==========================================
        self.scrollAreaRight = QtWidgets.QScrollArea(self.centralwidget)
        self.scrollAreaRight.setStyleSheet('background-color:"white";\ncolor:"black";')
        self.scrollAreaRight.setWidgetResizable(True)
        self.scrollAreaRight.setObjectName("scrollAreaRight")

        self.scrollAreaWidgetContentsRight = QtWidgets.QWidget()
        self.scrollAreaWidgetContentsRight.setGeometry(QtCore.QRect(0, 0, 239, 621))
        self.scrollAreaWidgetContentsRight.setObjectName(
            "scrollAreaWidgetContentsRight"
        )

        self.verticalLayout_2 = QtWidgets.QVBoxLayout(
            self.scrollAreaWidgetContentsRight
        )
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.verticalLayout_2.setSpacing(12)
        self.scrollAreaWidgetContentsRight.setStyleSheet(PANEL_STYLE)

        self.groupBox_Details = QtWidgets.QGroupBox(self.scrollAreaWidgetContentsRight)
        self.groupBox_Details.setObjectName("groupBox_Details")
        self.layout_Details = QtWidgets.QVBoxLayout(self.groupBox_Details)
        self.plainTextEdit_Details = QtWidgets.QPlainTextEdit(self.groupBox_Details)
        self.plainTextEdit_Details.setObjectName("plainTextEdit_Details")
        self.layout_Details.addWidget(self.plainTextEdit_Details)
        self.verticalLayout_2.addWidget(self.groupBox_Details)

        # The console draws its own "Process Log" header, so it gets a border
        # without a group-box title.
        self.frame_Logs = QtWidgets.QFrame(self.scrollAreaWidgetContentsRight)
        self.frame_Logs.setObjectName("frame_Logs")
        self.frame_Logs.setStyleSheet(FRAME_STYLE.format(name="frame_Logs"))
        self.layout_Logs = QtWidgets.QVBoxLayout(self.frame_Logs)
        self.layout_Logs.setContentsMargins(10, 10, 10, 10)

        # Structured console: colour-coded levels, severity filter, copy/save.
        self.plainTextEdit_Logs = LogPanel(self.frame_Logs)
        self.plainTextEdit_Logs.setObjectName("plainTextEdit_Logs")
        self.layout_Logs.addWidget(self.plainTextEdit_Logs)
        self.verticalLayout_2.addWidget(self.frame_Logs)

        # 0 = feature details, 1 = log console.
        self.verticalLayout_2.setStretch(0, 1)
        self.verticalLayout_2.setStretch(1, 2)
        self.scrollAreaRight.setWidget(self.scrollAreaWidgetContentsRight)

        # Three columns on wide screens; results stacked over the log on
        # narrow ones. No column ever scrolls sideways.
        columns = (self.scrollAreaLeft, self.scrollAreaMiddle, self.scrollAreaRight)
        self._width_trackers = [no_horizontal_scroll(area) for area in columns]
        self.columns = ResponsiveColumns(*columns, parent=self.centralwidget)
        self.columns.setObjectName("columns")
        self.verticalLayout_3.addWidget(self.columns, 1)

        self.widgetBottom = QtWidgets.QWidget(self.centralwidget)
        self.widgetBottom.setStyleSheet("QWidget { background-color: white; }")
        self.widgetBottom.setObjectName("widgetBottom")
        self.gridLayout_3 = QtWidgets.QGridLayout(self.widgetBottom)
        self.gridLayout_3.setObjectName("gridLayout_3")
        self.pushButton_Cancel = QtWidgets.QPushButton(self.widgetBottom)
        self.pushButton_Cancel.setObjectName("pushButton_Cancel")
        self.gridLayout_3.addWidget(self.pushButton_Cancel, 0, 3, 1, 1)
        self.pushButton_Run = QtWidgets.QPushButton(self.widgetBottom)
        self.pushButton_Run.setObjectName("pushButton_Run")
        self.gridLayout_3.addWidget(self.pushButton_Run, 0, 2, 1, 1)
        self.progressBar = QtWidgets.QProgressBar(self.widgetBottom)
        self.progressBar.setProperty("value", 0)
        self.progressBar.setAlignment(QtCore.Qt.AlignCenter)
        self.progressBar.setObjectName("progressBar")
        self.gridLayout_3.addWidget(self.progressBar, 0, 0, 1, 2)
        self.gridLayout_3.setColumnStretch(0, 8)
        self.verticalLayout_3.addWidget(self.widgetBottom)
        MainWindow.setCentralWidget(self.centralwidget)

        self.menubar = QtWidgets.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 990, 33))
        self.menubar.setObjectName("menubar")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        self.statusbar.setObjectName("statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        _translate = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_translate("MainWindow", "MLPREP Detection Suite"))
        self.groupBox_Parameters.setTitle(_translate("MainWindow", "Parameters"))
        self.groupBox_Details.setTitle(_translate("MainWindow", "Feature Details"))
        self.label_TableTitle.setText(
            _translate("MainWindow", "Top 10 Most Landslide Susceptible Municipalities")
        )
        self.label_TableCaption.setText(
            _translate("MainWindow", "Ranked by the number of High-hazard units")
        )
        self.label_HazardEmpty.setText(
            _translate("MainWindow", "Run the pipeline to rank municipalities by hazard.")
        )
        self.label_ChartTitle.setText(
            _translate("MainWindow", "Top 10 Hazard Distribution")
        )
        self.label_ChartCaption.setText(
            _translate("MainWindow", "Units per hazard class, stacked")
        )

        self.label_DEM.setText(_translate("MainWindow", "DEM"))
        self.pushButton_DEM.setText(_translate("MainWindow", "..."))

        parameters = [
            ("BulkDensity", "Bulk Density"),
            ("Clay", "Clay"),
            ("Sand", "Sand"),
            ("Silt", "Silt"),
            ("Slope", "Slope"),
            ("PGA", "Peak Ground Acceleration (PGA)"),
            ("PRC", "Precipitation"),
            ("SoilThickness", "Soil Thickness"),
            ("SoilType", "Soil Type / Lithology"),
            ("ContributingFactor", "Contributing Factor"),
            # Read only by the depositional zone's ML-DFI model, not the PINN.
            ("NDVI", "NDVI (depositional zone)"),
        ]

        for name, title in parameters:
            getattr(self, f"label_{name}").setText(_translate("MainWindow", title))
            getattr(self, f"pushButton_{name}").setText(_translate("MainWindow", "..."))

        self.groupBox_SlopeUnit.setTitle(_translate("MainWindow", "Slope Unit Tuning"))
        self.label_MaxIteration.setText(_translate("MainWindow", "Max Iteration"))

        self.groupBox_Model.setTitle(_translate("MainWindow", "PINN Model"))

        self.groupBox_Option.setTitle(_translate("MainWindow", "Output Setup"))
        self.label_OutputFolder.setText(
            _translate("MainWindow", "Save Output Files To:")
        )
        self.plainTextEdit_OutputFolder.setPlaceholderText(
            _translate("MainWindow", "[Defaults to DEM folder if left blank]")
        )
        self.pushButton_OutputFolder.setText(_translate("MainWindow", "..."))
        self.checkBox_LoadResults.setText(
            _translate("MainWindow", "Automatically load results into map")
        )

        self.pushButton_Cancel.setText(_translate("MainWindow", "Cancel"))
        self.pushButton_Run.setText(_translate("MainWindow", "Run Pipeline"))
