# -*- coding: utf-8 -*-

# qgis.PyQt maps to whichever Qt binding this QGIS was built against.
# Importing PyQt5 directly breaks on a Qt6 QGIS at plugin-load time.
from qgis.PyQt import QtCore, QtGui, QtWidgets


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(991, 765)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(MainWindow.sizePolicy().hasHeightForWidth())
        MainWindow.setSizePolicy(sizePolicy)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.centralwidget.sizePolicy().hasHeightForWidth())
        self.centralwidget.setSizePolicy(sizePolicy)
        self.centralwidget.setObjectName("centralwidget")
        self.horizontalLayout = QtWidgets.QHBoxLayout(self.centralwidget)
        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout.setSpacing(0)
        self.horizontalLayout.setObjectName("horizontalLayout")
        
        # ==========================================
        # LEFT SIDE: ISOLATED RESULT MAP AREA
        # ==========================================
        self.Widget_Map_Area = QtWidgets.QWidget(self.centralwidget)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(3)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.Widget_Map_Area.sizePolicy().hasHeightForWidth())
        self.Widget_Map_Area.setSizePolicy(sizePolicy)
        self.Widget_Map_Area.setStyleSheet("QWidget { background-color:#ffffff; } QGroupBox { font: 600 12px \"Arial\"; color: #E0E0E0; border: 2px solid #555; border-radius: 10px; margin-top: 16px; background-color: transparent; padding: 12px; } QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 8px; background-color: #ffffff; color: black; position:absolute; margin-top:10px }")
        self.Widget_Map_Area.setObjectName("Widget_Map_Area")
        
        self.verticalLayout_Map = QtWidgets.QVBoxLayout(self.Widget_Map_Area)
        self.verticalLayout_Map.setObjectName("verticalLayout_Map")
        
        self.GroupBox_Result = QtWidgets.QGroupBox(self.Widget_Map_Area)
        self.GroupBox_Result.setObjectName("GroupBox_Result")
        self.horizontalLayout_13 = QtWidgets.QHBoxLayout(self.GroupBox_Result)
        self.horizontalLayout_13.setObjectName("horizontalLayout_13")
        self.GraphicsView_Result = QtWidgets.QGraphicsView(self.GroupBox_Result)
        self.GraphicsView_Result.setObjectName("GraphicsView_Result")
        self.horizontalLayout_13.addWidget(self.GraphicsView_Result)
        self.verticalLayout_Map.addWidget(self.GroupBox_Result)
        
        self.horizontalLayout.addWidget(self.Widget_Map_Area)

        # ==========================================
        # RIGHT SIDE: SCROLL AREA & INPUTS
        # ==========================================
        self.GridLayout_Inputs_Area = QtWidgets.QGridLayout()
        self.GridLayout_Inputs_Area.setObjectName("GridLayout_Inputs_Area")
        self.scrollArea = QtWidgets.QScrollArea(self.centralwidget)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        sizePolicy.setHorizontalStretch(1)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.scrollArea.sizePolicy().hasHeightForWidth())
        self.scrollArea.setSizePolicy(sizePolicy)
        self.scrollArea.setStyleSheet("background-color:#D7D5D2;")
        self.scrollArea.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setObjectName("scrollArea")
        
        self.scrollAreaWidgetContents_5 = QtWidgets.QWidget()
        self.scrollAreaWidgetContents_5.setGeometry(QtCore.QRect(0, 0, 511, 701))
        self.scrollAreaWidgetContents_5.setObjectName("scrollAreaWidgetContents_5")
        self.verticalLayout_5 = QtWidgets.QVBoxLayout(self.scrollAreaWidgetContents_5)
        self.verticalLayout_5.setObjectName("verticalLayout_5")
        
        self.Widget_Inputs = QtWidgets.QWidget(self.scrollAreaWidgetContents_5)
        self.Widget_Inputs.setStyleSheet("QGroupBox { font: 600 12px \"Arial\"; color: #E0E0E0; border: 2px solid #555; border-radius: 10px; margin-top: 16px; background-color: transparent; padding: 12px; } QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 8px; background-color: #D7D5D2; color: black; position:absolute; margin-top:10px } QLabel { color: black; }")
        self.Widget_Inputs.setObjectName("Widget_Inputs")
        
        self.verticalLayout_4 = QtWidgets.QVBoxLayout(self.Widget_Inputs)
        self.verticalLayout_4.setObjectName("verticalLayout_4")
        
        self.Lable_Landslide = QtWidgets.QLabel(self.Widget_Inputs)
        self.Lable_Landslide.setMinimumSize(QtCore.QSize(250, 35))
        self.Lable_Landslide.setMaximumSize(QtCore.QSize(16777215, 35))
        self.Lable_Landslide.setStyleSheet("QLabel { color: black; font: 700 16pt \"Arial\"; }")
        self.Lable_Landslide.setAlignment(QtCore.Qt.AlignCenter)
        self.Lable_Landslide.setObjectName("Lable_Landslide")
        self.verticalLayout_4.addWidget(self.Lable_Landslide)
        
        # --- FILE INPUTS ---
        self.GroupBox_File_Inputs = QtWidgets.QGroupBox(self.Widget_Inputs)
        self.GroupBox_File_Inputs.setStyleSheet("QPushButton { font:montserrat; border: 1px solid #3a3a3a; border-top-left-radius: 6px; border-bottom-left-radius: 6px; border-top-right-radius: 0px; border-bottom-right-radius: 0px; background: #ffffff; color:black; padding: 6px 14px; font-weight: 10; font-size: 10px; min-height: 10; min-width: 55; } QPlainTextEdit { border: 1px solid #3a3a3a; border-left: 0; border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-top-left-radius: 0; border-bottom-left-radius: 0; background: #D5D5D5; padding: 0px 0px; min-height: 10; font-size: 10px; font-weight:10; color:black; } QPushButton:hover { background: #f4f4f4; } QPushButton:pressed { background: #eaeaea; }")
        self.GroupBox_File_Inputs.setObjectName("GroupBox_File_Inputs")
        self.gridLayout_2 = QtWidgets.QGridLayout(self.GroupBox_File_Inputs)
        self.gridLayout_2.setVerticalSpacing(15)
        self.gridLayout_2.setContentsMargins(10, 15, 10, 15)
        self.gridLayout_2.setObjectName("gridLayout_2")
        
        # DEM Input
        self.Label_DEM = QtWidgets.QLabel(self.GroupBox_File_Inputs)
        self.Label_DEM.setObjectName("Label_DEM")
        self.gridLayout_2.addWidget(self.Label_DEM, 0, 0, 1, 1)
        self.Widget_DEM = QtWidgets.QWidget(self.GroupBox_File_Inputs)
        self.horizontalLayout_5 = QtWidgets.QHBoxLayout(self.Widget_DEM)
        self.horizontalLayout_5.setContentsMargins(0, 0, 0, 0)
        self.PushButton_DEM = QtWidgets.QPushButton(self.Widget_DEM)
        self.PushButton_DEM.setObjectName("PushButton_DEM")
        self.horizontalLayout_5.addWidget(self.PushButton_DEM)
        self.PTE_DEM = QtWidgets.QPlainTextEdit(self.Widget_DEM)
        self.PTE_DEM.setMaximumSize(QtCore.QSize(16777215, 25))
        self.PTE_DEM.setObjectName("PTE_DEM")
        self.horizontalLayout_5.addWidget(self.PTE_DEM)
        self.gridLayout_2.addWidget(self.Widget_DEM, 0, 1, 1, 1)

        # ROI / LULC Input
        self.Label_ROI = QtWidgets.QLabel(self.GroupBox_File_Inputs)
        self.Label_ROI.setObjectName("Label_ROI")
        self.gridLayout_2.addWidget(self.Label_ROI, 1, 0, 1, 1)
        self.Widget_ROI = QtWidgets.QWidget(self.GroupBox_File_Inputs)
        self.horizontalLayout_4 = QtWidgets.QHBoxLayout(self.Widget_ROI)
        self.horizontalLayout_4.setContentsMargins(0, 0, 0, 0)
        self.PushButton_ROI = QtWidgets.QPushButton(self.Widget_ROI)
        self.PushButton_ROI.setObjectName("PushButton_ROI")
        self.horizontalLayout_4.addWidget(self.PushButton_ROI)
        self.PTE_ROI = QtWidgets.QPlainTextEdit(self.Widget_ROI)
        self.PTE_ROI.setMaximumSize(QtCore.QSize(16777215, 25))
        self.PTE_ROI.setObjectName("PTE_ROI")
        self.horizontalLayout_4.addWidget(self.PTE_ROI)
        self.gridLayout_2.addWidget(self.Widget_ROI, 1, 1, 1, 1)
        
        # Post-Event Input
        self.Label_Sentinel_2_Post_Event = QtWidgets.QLabel(self.GroupBox_File_Inputs)
        self.Label_Sentinel_2_Post_Event.setObjectName("Label_Sentinel_2_Post_Event")
        self.gridLayout_2.addWidget(self.Label_Sentinel_2_Post_Event, 2, 0, 1, 1)
        self.Widget_Sentinel_2_Post_Event = QtWidgets.QWidget(self.GroupBox_File_Inputs)
        self.horizontalLayout_9 = QtWidgets.QHBoxLayout(self.Widget_Sentinel_2_Post_Event)
        self.horizontalLayout_9.setContentsMargins(0, 0, 0, 0)
        self.PushButton_Sentinel_2_Post_Event = QtWidgets.QPushButton(self.Widget_Sentinel_2_Post_Event)
        self.PushButton_Sentinel_2_Post_Event.setObjectName("PushButton_Sentinel_2_Post_Event")
        self.horizontalLayout_9.addWidget(self.PushButton_Sentinel_2_Post_Event)
        self.PTE_Sentinel_2_Post_Event = QtWidgets.QPlainTextEdit(self.Widget_Sentinel_2_Post_Event)
        self.PTE_Sentinel_2_Post_Event.setMaximumSize(QtCore.QSize(16777215, 25))
        self.PTE_Sentinel_2_Post_Event.setObjectName("PTE_Sentinel_2_Post_Event")
        self.horizontalLayout_9.addWidget(self.PTE_Sentinel_2_Post_Event)
        self.gridLayout_2.addWidget(self.Widget_Sentinel_2_Post_Event, 2, 1, 1, 1)

        self.verticalLayout_4.addWidget(self.GroupBox_File_Inputs)

        # ==========================================
        # 2x2 PREVIEW GRID
        # ==========================================
        self.GroupBox_Preview = QtWidgets.QGroupBox(self.Widget_Inputs)
        self.GroupBox_Preview.setMinimumHeight(400) # Give the grid enough space to breathe
        self.GroupBox_Preview.setObjectName("GroupBox_Preview")
        
        self.gridLayout_Preview = QtWidgets.QGridLayout(self.GroupBox_Preview)
        self.gridLayout_Preview.setObjectName("gridLayout_Preview")
        
        # [0, 0] Post-Event
        self.GroupBox_Post_Event = QtWidgets.QGroupBox(self.GroupBox_Preview)
        self.GroupBox_Post_Event.setObjectName("GroupBox_Post_Event")
        self.horizontalLayout_11 = QtWidgets.QHBoxLayout(self.GroupBox_Post_Event)
        self.GraphicsView_Post_Event = QtWidgets.QGraphicsView(self.GroupBox_Post_Event)
        self.GraphicsView_Post_Event.setObjectName("GraphicsView_Post_Event")
        self.horizontalLayout_11.addWidget(self.GraphicsView_Post_Event)
        self.gridLayout_Preview.addWidget(self.GroupBox_Post_Event, 0, 0, 1, 1)
        
        # [0, 1] DEM
        self.GroupBox_DEM_2 = QtWidgets.QGroupBox(self.GroupBox_Preview)
        self.GroupBox_DEM_2.setObjectName("GroupBox_DEM_2")
        self.horizontalLayout_7 = QtWidgets.QHBoxLayout(self.GroupBox_DEM_2)
        self.GraphicsView_DEM_2 = QtWidgets.QGraphicsView(self.GroupBox_DEM_2)
        self.GraphicsView_DEM_2.setObjectName("GraphicsView_DEM_2")
        self.horizontalLayout_7.addWidget(self.GraphicsView_DEM_2)
        self.gridLayout_Preview.addWidget(self.GroupBox_DEM_2, 0, 1, 1, 1)

        # [1, 0] Slope
        self.GroupBox_Slope = QtWidgets.QGroupBox(self.GroupBox_Preview)
        self.GroupBox_Slope.setObjectName("GroupBox_Slope")
        self.horizontalLayout_Slope = QtWidgets.QHBoxLayout(self.GroupBox_Slope)
        self.GraphicsView_Slope = QtWidgets.QGraphicsView(self.GroupBox_Slope)
        self.GraphicsView_Slope.setObjectName("GraphicsView_Slope")
        self.horizontalLayout_Slope.addWidget(self.GraphicsView_Slope)
        self.gridLayout_Preview.addWidget(self.GroupBox_Slope, 1, 0, 1, 1)
        
        # [1, 1] LULC (ROI)
        self.GroupBox_ROI = QtWidgets.QGroupBox(self.GroupBox_Preview)
        self.GroupBox_ROI.setObjectName("GroupBox_ROI")
        self.horizontalLayout_10 = QtWidgets.QHBoxLayout(self.GroupBox_ROI)
        self.GraphicsView_ROI = QtWidgets.QGraphicsView(self.GroupBox_ROI)
        self.GraphicsView_ROI.setObjectName("GraphicsView_ROI")
        self.horizontalLayout_10.addWidget(self.GraphicsView_ROI)
        self.gridLayout_Preview.addWidget(self.GroupBox_ROI, 1, 1, 1, 1)
        
        self.verticalLayout_4.addWidget(self.GroupBox_Preview)

        # --- FOOTER BUTTONS ---
        self.Widget_Footer_Buttons = QtWidgets.QWidget(self.Widget_Inputs)
        self.Widget_Footer_Buttons.setStyleSheet("QPushButton { font: montserrat; border: 1px solid #3a3a3a; border-radius: 6px; background: #ffffff; color:black; padding: 6px 14px; font-weight: 10; font-size: 10px; min-height: 10; min-width: 55; } QPushButton:hover { background: #f4f4f4; }")
        self.Widget_Footer_Buttons.setObjectName("Widget_Footer_Buttons")
        self.horizontalLayout_3 = QtWidgets.QHBoxLayout(self.Widget_Footer_Buttons)
        self.horizontalLayout_3.setObjectName("horizontalLayout_3")
        self.Button_Save = QtWidgets.QPushButton(self.Widget_Footer_Buttons)
        self.Button_Save.setObjectName("Button_Save")
        self.horizontalLayout_3.addWidget(self.Button_Save)
        self.Button_Detect = QtWidgets.QPushButton(self.Widget_Footer_Buttons)
        self.Button_Detect.setObjectName("Button_Detect")
        self.horizontalLayout_3.addWidget(self.Button_Detect)
        self.verticalLayout_4.addWidget(self.Widget_Footer_Buttons)
        
        self.verticalLayout_5.addWidget(self.Widget_Inputs)
        self.scrollArea.setWidget(self.scrollAreaWidgetContents_5)
        self.GridLayout_Inputs_Area.addWidget(self.scrollArea, 0, 0, 1, 1)
        self.horizontalLayout.addLayout(self.GridLayout_Inputs_Area)
        MainWindow.setCentralWidget(self.centralwidget)

        self.retranslateUi(MainWindow)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        _translate = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_translate("MainWindow", "MainWindow"))
        
        # Isolated Result Group
        self.GroupBox_Result.setTitle(_translate("MainWindow", "Final Landslide Result"))
        self.Lable_Landslide.setText(_translate("MainWindow", "Landslide Detection"))
        
        # File Inputs Group
        self.GroupBox_File_Inputs.setTitle(_translate("MainWindow", "File Inputs"))
        self.Label_DEM.setText(_translate("MainWindow", "DEM:"))
        self.PushButton_DEM.setText(_translate("MainWindow", "Choose File"))
        self.Label_ROI.setText(_translate("MainWindow", "LULC / ROI:"))
        self.PushButton_ROI.setText(_translate("MainWindow", "Choose File"))
        self.Label_Sentinel_2_Post_Event.setText(_translate("MainWindow", "Post-Event (S2):"))
        self.PushButton_Sentinel_2_Post_Event.setText(_translate("MainWindow", "Choose File"))
        
        # Preview Group
        self.GroupBox_Preview.setTitle(_translate("MainWindow", "Input Previews"))
        self.GroupBox_Post_Event.setTitle(_translate("MainWindow", "Post-Event"))
        self.GroupBox_DEM_2.setTitle(_translate("MainWindow", "DEM"))
        self.GroupBox_Slope.setTitle(_translate("MainWindow", "Slope"))
        self.GroupBox_ROI.setTitle(_translate("MainWindow", "LULC"))
        
        self.Button_Save.setText(_translate("MainWindow", "SAVE"))
        self.Button_Detect.setText(_translate("MainWindow", "DETECT"))


if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    MainWindow = QtWidgets.QMainWindow()
    ui = Ui_MainWindow()
    ui.setupUi(MainWindow)
    MainWindow.show()
    sys.exit(app.exec_())