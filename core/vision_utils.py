# FreeCadCopilot/core/vision_utils.py
import FreeCAD
import os

class VisionCapture:
    """
    Captures screenshots of the active 3D view for Execution Reviewer comparison.
    """
    @staticmethod
    def capture_multiview(temp_folder="/tmp"):
        """
        Takes Axometric, Top, and Front screenshots.
        Returns paths to the saved images.
        """
        try:
            import FreeCADGui
            view = FreeCADGui.ActiveDocument.ActiveView
        except (ImportError, AttributeError):
             return {"error": "No active GUI or View found to capture."}

        if not os.path.exists(temp_folder):
            os.makedirs(temp_folder)
            
        screenshots = {}
        
        # Original camera orientation
        orig_orientation = view.getCameraOrientation()
        
        try:
            # Axometric
            view.viewAxometric()
            FreeCADGui.updateGui()
            axo_path = os.path.join(temp_folder, "axo_view.png")
            view.saveImage(axo_path, 800, 600, "Transparent")
            screenshots["axo"] = axo_path
            
            # Top
            view.viewTop()
            FreeCADGui.updateGui()
            top_path = os.path.join(temp_folder, "top_view.png")
            view.saveImage(top_path, 800, 600, "Transparent")
            screenshots["top"] = top_path
            
            # Front
            view.viewFront()
            FreeCADGui.updateGui()
            front_path = os.path.join(temp_folder, "front_view.png")
            view.saveImage(front_path, 800, 600, "Transparent")
            screenshots["front"] = front_path
            
        finally:
            # Restore original view
            view.setCameraOrientation(orig_orientation)
            FreeCADGui.updateGui()
            
        return {"status": "success", "images": screenshots}
