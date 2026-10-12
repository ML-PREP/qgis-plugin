"""Stylesheet applied to the whole plugin window."""

MASTER_STYLESHEET = """
    QMainWindow { background-color: #2b2b2b; }
    QTabWidget::pane { border: none; background-color: #D7D5D2; }
    QTabBar::tab { background-color: #b0b0b0; color: black; padding: 10px 20px; font-weight: bold; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
    QTabBar::tab:selected { background-color: #D7D5D2; }

    QWidget#Widget_Map_Area { background-color: #ffffff; }
    QScrollArea#scrollArea, QWidget#Widget_Inputs { background-color: #D7D5D2; border: none; }

    QWidget#Widget_Map_Area QGroupBox, QWidget#Widget_Inputs QGroupBox { font: 600 12px "Arial"; color: #E0E0E0; border: 2px solid #555; border-radius: 10px; margin-top: 16px; background-color: transparent; padding: 12px; }
    QWidget#Widget_Map_Area QGroupBox::title { background-color: #ffffff; color: black; subcontrol-origin: margin; subcontrol-position: top center; padding: 0 8px; position: absolute; margin-top: 10px;}
    QWidget#Widget_Inputs QGroupBox::title { background-color: #D7D5D2; color: black; subcontrol-origin: margin; subcontrol-position: top center; padding: 0 8px; position: absolute; margin-top: 10px;}

    QLabel#Lable_Landslide { color: black; font: 700 16pt "Arial"; }

    QPushButton#Button_Detect, QPushButton#Button_Save { font-family: montserrat; border: 1px solid #3a3a3a; border-radius: 6px; background-color: #ffffff; color: black; padding: 6px 14px; font-weight: bold; font-size: 10px; min-height: 10px; min-width: 55px; }
    QComboBox#ComboBox_ROI_Level, QComboBox#ComboBox_ROI_Name { border: 1px solid #3a3a3a; border-radius: 6px; padding: 4px 10px; background-color: #D5D5D5; color: black; font-size: 11px; min-height: 25px; }
    QLineEdit#LineEdit_Search { border: 1px solid #3a3a3a; border-radius: 6px; padding: 4px 10px; background-color: #ffffff; color: black; font-size: 11px; min-height: 25px; }

    QWidget#scrollAreaWidgetContentsLeft QLabel, QWidget#scrollAreaWidgetContentsLeft QCheckBox { color: black; }
"""
