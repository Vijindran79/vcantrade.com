"""VcanTrade AI - Phase 2 Main Entry Point"""

import sys
import logging
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from ui.dashboard import CommandCenter, GlassOverlay
from core.database import DatabaseManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    app = QApplication(sys.argv)
    
    # Initialize database with autopsies table (fixes crash)
    db = DatabaseManager()
    db.initialize()
    
    # Create Command Center
    command_center = CommandCenter()
    command_center.show()
    
    # Create Glass Overlay
    glass_overlay = GlassOverlay(command_center)
    
    # Connect signals
    def on_eod_report():
        try:
            from core.analytics_reporter import AnalyticsReporter
            reporter = AnalyticsReporter(db)
            reporter.generate_eod_report()
            command_center.log("EOD Report generated successfully")
        except Exception as e:
            logger.error(f"EOD Report failed: {e}")
            command_center.log(f"[WARNING] EOD Report failed: {str(e)}")
    
    command_center.eod_report_requested.connect(on_eod_report)
    
    # Cleanup on exit
    def cleanup():
        db.close()
    
    app.aboutToQuit.connect(cleanup)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
