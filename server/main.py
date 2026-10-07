from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.utilities.types import Image as MCPImage
import json
import os
import time
import asyncio
import logging
import subprocess
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from typing import Dict, Any, Optional
import sys
import win32gui
import win32ui
import win32con
import win32api
import win32process
from PIL import Image
import io
import base64
import glob
import re
import locale
import uuid
import zipfile
from datetime import datetime, timezone
from step_export import export_step

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG for more detailed logs
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Output to console
        logging.FileHandler(str(Path(__file__).with_name('altium_mcp.log')))  # Also log to file
    ]
)
logger = logging.getLogger("AltiumMCPServer")

# Set MCP_DIR to the directory of the current Python file
MCP_DIR = Path(__file__).parent
# config.json lives in a per-user folder OUTSIDE the install directory and
# outside AppData. Claude Desktop replaces the extension folder on every
# update, which used to reset a hand-picked Altium path; and being an MSIX
# package it redirects its child processes' AppData writes into its own
# LocalCache. start_server.py passes the folder it chose in ALTIUM_MCP_HOME.
# LEGACY_CONFIG_FILE is read once to migrate.
def _data_dir() -> Path:
    override = os.environ.get("ALTIUM_MCP_HOME")
    return Path(override) if override else Path.home() / ".altium-mcp"

LEGACY_CONFIG_FILE = MCP_DIR / "config.json"
CONFIG_FILE = _data_dir() / "config.json"
DEFAULT_SCRIPT_PATH = MCP_DIR / "AltiumScript" / "Altium_API.PrjScr"

# Use a fixed exchange directory for request/response JSON files.
# Both the Python MCP server and the Altium DelphiScript need to independently
# resolve to the same directory. C:\Users\Public is writable by all users and
# exists on every Windows machine. This avoids fragile script-project-path
# resolution that breaks when Altium caches stale script projects.
EXCHANGE_DIR = Path("C:/Users/Public/altium_mcp")
EXCHANGE_DIR.mkdir(exist_ok=True)
REQUEST_FILE = EXCHANGE_DIR / "request.json"
RESPONSE_FILE = EXCHANGE_DIR / "response.json"

# Initialize FastMCP server
mcp = FastMCP("AltiumMCP", description="Altium integration through the Model Context Protocol")

class AltiumConfig:
    def __init__(self):
        self.altium_exe_path = ""
        self.script_path = str(DEFAULT_SCRIPT_PATH)
        self.load_config()
    
    def load_config(self):
        """Load configuration, migrating a pre-relocation config.json once."""
        source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
        if source.exists():
            try:
                with open(source, "r") as f:
                    config = json.load(f)
                self.altium_exe_path = config.get("altium_exe_path", "")
                # Only a hand-picked script project is persisted. A saved path
                # that no longer exists (old install folder) falls back to the
                # project shipped beside this file instead of prompting.
                saved_script = config.get("script_path", "")
                if saved_script and os.path.exists(saved_script):
                    self.script_path = saved_script
                logger.info(f"Loaded configuration from {source}")
                if source is LEGACY_CONFIG_FILE:
                    self.save_config()
                else:
                    self._saved = self._as_dict()
            except Exception as e:
                logger.error(f"Error loading configuration: {e}")
                self._create_default_config()
        else:
            logger.info("No configuration file found, creating default")
            self._create_default_config()

    def _create_default_config(self):
        """Create a default configuration file with improved Altium executable discovery"""
        
        # Try to find Altium directories dynamically
        altium_base_path = r"C:\Program Files\Altium"
        altium_exe_path = None
        
        if os.path.exists(altium_base_path):
            # Find all directories that match the pattern AD*
            ad_dirs = glob.glob(os.path.join(altium_base_path, "AD*"))
            
            if ad_dirs:
                # Sort directories by version number (extract the number after "AD")
                def get_version_number(dir_path):
                    match = re.search(r"AD(\d+)", os.path.basename(dir_path))
                    if match:
                        return int(match.group(1))
                    return 0
                
                # Sort directories by version number (highest first)
                ad_dirs.sort(key=get_version_number, reverse=True)
                
                # Try each directory until we find one with X2.EXE
                for ad_dir in ad_dirs:
                    potential_exe = os.path.join(ad_dir, "X2.EXE")
                    if os.path.exists(potential_exe):
                        altium_exe_path = potential_exe
                        break
        
        # Set the found path (or empty string if nothing found)
        self.altium_exe_path = altium_exe_path if altium_exe_path else ""
        
        # Save the configuration
        self.save_config()
    
    def _as_dict(self):
        config = {"altium_exe_path": self.altium_exe_path}
        # The default script project belongs to whichever install is running
        # (extension folder or a dev checkout), so it is never written down:
        # both share this file and must not steal each other's scripts.
        if Path(self.script_path) != DEFAULT_SCRIPT_PATH:
            config["script_path"] = self.script_path
        return config

    def save_config(self):
        """Save configuration to file, only when something changed"""
        config = self._as_dict()
        if config == getattr(self, "_saved", None) and CONFIG_FILE.exists():
            return

        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            self._saved = config
            logger.info(f"Saved configuration to {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    def verify_paths(self):
        """Verify that the paths in the configuration exist, prompt for input if they don't"""

        # Initialize variables
        root = None
        paths_verified = True
        
        # Check Altium executable
        if not self.altium_exe_path or not os.path.exists(self.altium_exe_path):
            paths_verified = False
            
            # Before prompting, try an automatic discovery
            altium_base_path = r"C:\Program Files\Altium"
            if os.path.exists(altium_base_path):
                logger.info(f"Attempting automatic discovery in {altium_base_path}")
                # Find all directories that match the pattern AD*
                ad_dirs = glob.glob(os.path.join(altium_base_path, "AD*"))
                
                if ad_dirs:
                    # Sort directories by version number (extract the number after "AD")
                    def get_version_number(dir_path):
                        match = re.search(r"AD(\d+)", os.path.basename(dir_path))
                        if match:
                            return int(match.group(1))
                        return 0
                    
                    # Sort directories by version number (highest first)
                    ad_dirs.sort(key=get_version_number, reverse=True)
                    
                    # Try each directory until we find one with X2.EXE
                    for ad_dir in ad_dirs:
                        potential_exe = os.path.join(ad_dir, "X2.EXE")
                        if os.path.exists(potential_exe):
                            self.altium_exe_path = potential_exe
                            logger.info(f"Automatically found Altium at: {self.altium_exe_path}")
                            print(f"Automatically found Altium at: {self.altium_exe_path}")
                            paths_verified = True
                            break
            
            # If automatic discovery failed, prompt for input
            if not self.altium_exe_path or not os.path.exists(self.altium_exe_path):
                if root is None:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()  # Hide the main window
                
                logger.info("Altium executable not found. Prompting user for selection...")
                print(f"Altium executable not found. Searched in:")
                print(f"  - Automatically scanned C:\\Program Files\\Altium\\AD*\\X2.EXE")
                print(f"  - Last known path: {self.altium_exe_path}")
                print("Please select the Altium X2.EXE file...")
                
                self.altium_exe_path = filedialog.askopenfilename(
                    title="Select Altium Executable",
                    filetypes=[("Executable files", "*.exe")],  # Only allow .exe files
                    initialdir="C:/Program Files/Altium"
                )
                
                if not self.altium_exe_path:
                    logger.error("No Altium executable selected. Some functionality may not work.")
                    print("Warning: No Altium executable selected. Automatic script execution will be disabled.")
                    paths_verified = False
        
        # Check script path
        if not os.path.exists(self.script_path):
            paths_verified = False
            
            if root is None:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()  # Hide the main window
            
            logger.info(f"Script file not found at {self.script_path}. Prompting user for selection...")
            print(f"Script file not found at {self.script_path}. Please select the Altium project file...")
            
            selected_path = filedialog.askopenfilename(
                title="Select Altium Project File",
                filetypes=[("Altium Project files", "*.PrjScr")],  # Changed to PrjScr for script project
                initialdir=str(MCP_DIR)
            )
            
            if selected_path:
                self.script_path = selected_path
            else:
                logger.error("No script file selected. Some functionality may not work.")
                print("Warning: No script file selected. Please make sure to create one.")
                paths_verified = False
        
        # Clean up tkinter root if created
        if root is not None:
            root.destroy()
        
        # Save the updated configuration
        self.save_config()
        
        return paths_verified

class AltiumBridge:
    def __init__(self):
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)

        # Load configuration
        self.config = AltiumConfig()
        self.config.verify_paths()

        # Commands share a single request.json/response.json pair, so
        # concurrent tool calls must be serialized or they clobber each other
        self._command_lock = asyncio.Lock()

    async def execute_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a command in Altium via the bridge script"""
        async with self._command_lock:
            return await self._execute_command_locked(command, params)

    async def _execute_command_locked(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # Clean up any existing response file
            if RESPONSE_FILE.exists():
                RESPONSE_FILE.unlink()
            
            # Write the request file with command and parameters
            with open(REQUEST_FILE, "w") as f:
                json.dump({
                    "command": command,
                    **params  # Include parameters directly in the main JSON object
                }, f, indent=2)
            
            logger.info(f"Wrote request file for command: {command}")
            
            # Run the Altium script
            success = await self.run_altium_script()
            if not success:
                return {"success": False, "error": "Failed to run Altium script"}
            
            # Wait for the response file
            logger.info(f"Waiting for response file to appear...")
            timeout = 120  # seconds
            start_time = time.time()
            while not RESPONSE_FILE.exists() and time.time() - start_time < timeout:
                await asyncio.sleep(0.5)
            
            if not RESPONSE_FILE.exists():
                logger.error("Timeout waiting for response from Altium")
                return {"success": False, "error": "No response received from Altium (timeout)"}
            
            # Read the response file and print it for debugging
            logger.info("Response file found, reading response")
            response_text = ""
            with open(RESPONSE_FILE, "r") as f:
                response_text = f.read()
            
            # Log the raw response for debugging
            logger.info(f"Raw response (first 200 chars): {response_text[:200]}")
            
            # Parse the JSON response with detailed error handling
            try:
                response = json.loads(response_text)
                logger.info(f"Successfully parsed JSON response")
                return response
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing JSON response: {e}")
                logger.error(f"Error at position {e.pos}, line {e.lineno}, column {e.colno}")
                logger.error(f"Character at error position: '{response_text[e.pos:e.pos+10]}...'")
                
                # Try to manually fix common JSON issues
                logger.info("Attempting to fix JSON response...")
                fixed_text = response_text
                
                # Fix 1: If there's a quoted JSON array, try to fix it
                if '"[' in fixed_text and ']"' in fixed_text:
                    fixed_text = fixed_text.replace('"[', '[').replace(']"', ']')
                    logger.info("Fixed double-quoted JSON array")
                
                # Fix 2: Handle escaped quotes in JSON strings
                fixed_text = fixed_text.replace('\\"', '"')
                
                # Try to parse the fixed JSON
                try:
                    fixed_response = json.loads(fixed_text)
                    logger.info("Successfully parsed fixed JSON response")
                    return fixed_response
                except json.JSONDecodeError as e2:
                    logger.error(f"Still failed to parse JSON after fixes: {e2}")
                
                # If all else fails, return a structured error
                return {
                    "success": False, 
                    "error": f"Invalid JSON response: {e}",
                    "raw_response": response_text[:500]  # Include part of the raw response for diagnosis
                }
        
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def _resolve_msix_path(virtual_path: str) -> str:
        """Resolve an MSIX-virtualized path to the real filesystem path.

        When Claude Desktop is installed via MSIX (the standard .exe installer
        on modern Windows), file paths are virtualized under AppData\\Roaming\\
        but the real files live at AppData\\Local\\Packages\\Claude_*\\
        LocalCache\\Roaming\\. Child processes of the MSIX app (like Python)
        can see the virtualized paths, but external apps (like Altium) cannot.
        This resolves the path so external processes can find the files.
        """
        appdata = os.environ.get('APPDATA', '')
        if not appdata or not virtual_path.startswith(appdata):
            return virtual_path

        localappdata = os.environ.get('LOCALAPPDATA', '')
        packages_dir = os.path.join(localappdata, 'Packages')
        if not os.path.isdir(packages_dir):
            return virtual_path

        try:
            for item in os.listdir(packages_dir):
                if item.startswith('Claude_'):
                    relative = os.path.relpath(virtual_path, appdata)
                    real_path = os.path.join(packages_dir, item, 'LocalCache', 'Roaming', relative)
                    if os.path.exists(real_path):
                        logger.info(f"Resolved MSIX path: {virtual_path} -> {real_path}")
                        return real_path
        except Exception as e:
            logger.warning(f"Error resolving MSIX path: {e}")

        return virtual_path

    async def run_altium_script(self) -> bool:
        """Run the Altium bridge script"""
        if not os.path.exists(self.config.altium_exe_path):
            logger.error(f"Altium executable not found at: {self.config.altium_exe_path}")
            print(f"Error: Altium executable not found. Please check the configuration.")
            return False

        if not os.path.exists(self.config.script_path):
            logger.error(f"Script file not found at: {self.config.script_path}")
            print(f"Error: Script file not found. Please check the configuration.")
            return False

        try:
            # Resolve MSIX-virtualized path so Altium (an external process
            # outside the MSIX sandbox) can find the script files
            script_path = self._resolve_msix_path(self.config.script_path)

            # Command format: "X2.EXE" -RScriptingSystem:RunScript(ProjectName="path\file.PrjScr"|ProcName="ModuleName>Run")
            command = f'"{self.config.altium_exe_path}" -RScriptingSystem:RunScript(ProjectName="{script_path}"^|ProcName="Altium_API>Run")'
            
            logger.info(f"Running command: {command}")
            
            # Start the process. Altium must not inherit this server's stdio:
            # they are the MCP transport, and a child holding them keeps the
            # pipes open after the server exits.
            process = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Don't wait for completion - Altium will run the script and generate the response
            logger.info(f"Launched Altium with script, process ID: {process.pid}")
            return True
        
        except Exception as e:
            logger.error(f"Error launching Altium: {e}")
            return False

# Create a global bridge instance
altium_bridge = AltiumBridge()

@mcp.tool()
async def get_all_component_property_names(ctx: Context) -> str:
    """
    Get all available component property names (JSON keys) from all components
    
    Returns:
        str: JSON array with all unique property names
    """
    logger.info("Getting all component property names")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "get_all_component_data", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", [])
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
            
        # Extract all unique property names from all components
        property_names = set()
        for component in components_list:
            property_names.update(component.keys())
        
        # Convert set to sorted list for consistent output
        property_list = sorted(list(property_names))
        
        logger.info(f"Found {len(property_list)} unique property names")
        return json.dumps(property_list, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_component_property_values(ctx: Context, property_name: str) -> str:
    """
    Get values of a specific property for all components
    
    Args:
        property_name (str): The name of the property to get values for
    
    Returns:
        str: JSON array with objects containing designator and property value
    """
    logger.info(f"Getting values for property: {property_name}")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "get_all_component_data", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", [])
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
            
        # Extract the property values along with designators
        property_values = []
        for component in components_list:
            designator = component.get("designator")
            if designator and property_name in component:
                property_values.append({
                    "designator": designator,
                    "value": component.get(property_name)
                })
        
        logger.info(f"Found {len(property_values)} components with property '{property_name}'")
        return json.dumps(property_values, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})
    
@mcp.tool()
async def get_symbol_placement_rules(ctx: Context) -> str:
    """
    Get schematic symbol placement rules from a local configuration file
    
    Returns:
        str: JSON object with rules for placing pins on schematic symbols
    """
    logger.info("Getting symbol placement rules")
    
    # Define the rules file path in the MCP directory
    rules_file_path = MCP_DIR / "symbol_placement_rules.txt"
    
    # Check if the rules file exists
    if not rules_file_path.exists():
        logger.info("Symbol placement rules file not found, suggesting creation")
        
        # Default rules content
        default_rules = (
            "Only place pins on the left and right side of the symbol. "
            "Place power rail pins at the upper right, ground pins in the bottom left, "
            "no connect pins in the bottom right, inputs on the left, outputs on the right, "
            "and try to group other pins together by similar functionality (for example, SPI, I2C, RGMII, etc.). "
            "Always separate groups by 100mil gaps unless there is extra spacing, then space out groups equal distance from each other. "
        )
        
        # Create a helpful message for the user
        message = {
            "success": False,
            "error": f"Rules file not found at: {rules_file_path}",
            "message": f"Let the user know that they can optionally update the file {rules_file_path} with custom symbol placement rules. "
                      f"Suggested content: {default_rules}"
        }
        
        return json.dumps(message, indent=2)
    
    # Read the rules file if it exists
    try:
        with open(rules_file_path, "r") as f:
            rules_content = f.read()
        
        logger.info("Successfully read symbol placement rules file")
        
        # Return the rules with a message about how to modify them
        result = {
            "success": True,
            "message": f"Modify {rules_file_path} with custom symbol placement instructions",
            "rules": rules_content
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error reading symbol placement rules file: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to read rules file: {str(e)}"
        }, indent=2)

@mcp.tool()
async def get_library_symbol_reference(ctx: Context) -> str:
    """
    Get the currently open symbol from a schematic library to use as reference for creating a new symbol.
    This tool should be used before creating a new symbol to understand the structure of existing symbols.
    
    Returns:
        str: JSON object with the reference symbol data including pins, their types, positions, and orientations
    """
    logger.info("Getting library symbol reference data")
    
    # Execute the command in Altium to get symbol reference data
    response = await altium_bridge.execute_command(
        "get_library_symbol_reference", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting symbol reference: {error_msg}")
        return json.dumps({"error": f"Failed to get symbol reference: {error_msg}"})
    
    # Get the symbol reference data
    symbol_data = response.get("result", {})
    
    if not symbol_data:
        logger.info("No symbol reference data found")
        return json.dumps({"error": "No symbol reference data found or no symbol is currently selected in the library"})
    
    logger.info(f"Retrieved symbol reference data")
    return json.dumps(symbol_data, indent=2)

@mcp.tool()
async def search_library_symbol(ctx: Context, symbol_name: str, library_path: str = "") -> str:
    """
    Search for a symbol by name in a schematic library (.SchLib) and navigate to it.
    Supports partial name matching (case-insensitive). Returns all matches and navigates
    to the best match (exact match preferred, otherwise first partial match).

    This tool will automatically open the library file in Altium if a path is provided,
    so no SchLib needs to be open beforehand.

    Args:
        symbol_name (str): Name or partial name of the symbol to search for
        library_path (str): Full file path to the .SchLib file (e.g. "C:\\Libraries\\MyParts.SchLib").
                           The tool will open this file in Altium if it is not already open.
                           If empty, uses the currently open library.
                           If no library is open and no path is provided, ask the user for the file path.

    Returns:
        str: JSON object with search results including matches, navigated symbol, and full symbol list
    """
    logger.info(f"Searching for symbol: {symbol_name} in library: {library_path or '(current)'}")

    # Execute the command in Altium
    params = {"symbol_name": symbol_name}
    if library_path:
        params["library_path"] = library_path

    response = await altium_bridge.execute_command(
        "search_library_symbol",
        params
    )

    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error searching for symbol: {error_msg}")
        return json.dumps({"error": f"Failed to search for symbol: {error_msg}"})

    # Get the result data
    result = response.get("result", {})

    if not result:
        logger.info("No search results returned")
        return json.dumps({"error": "No results returned from symbol search"})

    logger.info(f"Symbol search complete. Found: {result.get('found', False)}")
    return json.dumps(result, indent=2)

@mcp.tool()
async def create_schematic_symbol(ctx: Context, symbol_name: str, description: str, pins: list, part_count: int = 1, graphics: list = None) -> str:
    r"""
    Before executing, run get_symbol_placement_rules first.

    For Altium API guidance while scripting, use the "altium-script" skill
    (ensure_altium_script_skill reports whether it is installed).

    Also look for a similar existing symbol to use as a style reference
    before drawing: search a company/library .SchLib for a comparable part
    (same category - op-amp, comparator, MCU, regulator, diode...) with
    search_library_symbol, then dump it with get_symbol_primitives and
    mirror its conventions (body style, pin lengths, pin name visibility,
    grid spacing, glyph shapes). This produces symbols consistent with the
    user's library. If no symbol library is available to reference, that is
    fine - skip this step and proceed with the defaults below; do not treat
    a missing reference as an error.

    Create a new schematic symbol in the current library with the specified pins
    Instructions: pins should be grouped together via function and only placed on
                  the left and right side in 100 mil increments

    Pin name inversion/overbar: To show an overbar on a pin name (for active-low signals),
                  place a backslash after EACH character that should be overbarred.
                  Examples: R\E\S\E\T\ renders as RESET with overbar.
                           C\S\/A0 renders as CS with overbar followed by /A0 without overbar.
                  Do NOT use ~{...} or other notation — only the backslash-per-character format works in Altium.

    Args:
        symbol_name (str): Name of the symbol to create
        description (str): Description of the schematic symbol
        pins (list): List of pin data in format
                    "pin_number|pin_name|pin_type|pin_orientation|x|y[|owner_part_id[|length[|show_name[|show_designator]]]]"
                    Pin types: eElectricHiZ, eElectricInput, eElectricIO, eElectricOpenCollector,
                               eElectricOpenEmitter, eElectricOutput, eElectricPassive, eElectricPower
                    Pin orientations: eRotate0 (right), eRotate90 (down), eRotate180 (left), eRotate270 (up)
                    X,Y coordinates in mils
                    owner_part_id (optional): Part number the pin belongs to (1-based).
                               Use 0 for pins shared across all parts (e.g. power/GND).
                               Defaults to 1 if omitted. Only needed for multi-part symbols.
                    length (optional): pin length in mils (default 300)
                    show_name / show_designator (optional): 1 or 0 to show/hide
                               the pin name / number (e.g. op-amp pins often hide names)
        part_count (int): Number of parts in the symbol (default 1).
                         Use >1 for multi-part symbols like quad op-amps or hex buffers.
        graphics (list, optional): Explicit body graphics. When given, the
                    default auto-sized body rectangle is NOT drawn - the
                    graphics fully define the symbol body (triangles for
                    op-amps, diode glyphs, etc.). Entry formats (coordinates
                    in mils; part = owner part id, 1-based; width 0-3 =
                    zero/small/medium/large; solid 1 or 0):
                    - "line|part|width|x1|y1|x2|y2"
                    - "polyline|part|width|x1|y1|x2|y2|..." (any number of vertices)
                    - "polygon|part|width|solid|x1|y1|x2|y2|..." (closed/filled shape)
                    - "rectangle|part|width|solid|x1|y1|x2|y2"
                    - "arc|part|width|cx|cy|radius|start_angle|end_angle" (degrees CCW from 3 o'clock)
                    - "elliptical_arc|part|width|cx|cy|radius|secondary_radius|start_angle|end_angle"
                    - "ellipse|part|width|solid|cx|cy|radius|secondary_radius"
                    - "label|part|x|y|text" (free text annotation)
                    Tip: to reproduce an existing symbol's style, dump it first
                    with get_symbol_primitives and mirror its primitives.

    Returns:
        str: JSON object with the result of the component creation
    """
    logger.info(f"Creating schematic symbol: {symbol_name} with {len(pins)} pins, {part_count} part(s)")

    params = {
        "symbol_name": symbol_name,
        "description": description,
        "part_count": part_count,
        "pins": pins
    }
    if graphics:
        params["graphics"] = graphics

    # Execute the command in Altium to create the symbol
    response = await altium_bridge.execute_command(
        "create_schematic_symbol",
        params
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating symbol: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create symbol: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Symbol {symbol_name} created successfully with {len(pins)} pins")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_schematic_data(ctx: Context, cmp_designators: list) -> str:
    """
    Get schematic data for components in Altium
    
    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])
    
    Returns:
        str: JSON object with schematic component data for requested designators
    """
    logger.info(f"Getting schematic data for components: {cmp_designators}")
    
    # Execute the command in Altium to get schematic data
    response = await altium_bridge.execute_command(
        "get_schematic_data",
        {}  # No parameters needed for this command in the Altium script
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting schematic data: {error_msg}")
        return json.dumps({"error": f"Failed to get schematic data: {error_msg}"})
    
    # Get the schematic data
    schematic_data = response.get("result", [])
    
    if not schematic_data:
        logger.info("No schematic data found")
        return json.dumps({"error": "No schematic data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(schematic_data, str):
            schematic_list = json.loads(schematic_data)
        else:
            schematic_list = schematic_data
        
        # Filter components by designator
        components = []
        missing_designators = []
        
        for designator in cmp_designators:
            found = False
            for component in schematic_list:
                if component.get("designator") == designator:
                    components.append(component)
                    found = True
                    break
            
            if not found:
                missing_designators.append(designator)
        
        result = {
            "components": components,
        }
        
        if missing_designators:
            result["missing_designators"] = missing_designators
            logger.info(f"Some designators not found in schematic data: {missing_designators}")
        
        logger.info(f"Found schematic data for {len(components)} components")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error processing schematic data: {e}")
        return json.dumps({"error": f"Failed to process schematic data: {str(e)}"})
    
@mcp.tool()
async def get_pcb_layers(ctx: Context) -> str:
    """
    Get detailed information about all layers in the current Altium PCB
    
    Returns:
        str: JSON object with detailed layer information including copper layers, 
             mechanical layers, and special layers with their properties
    """
    logger.info("Getting detailed PCB layer information")
    
    # Execute the command in Altium to get all layers data
    response = await altium_bridge.execute_command(
        "get_pcb_layers",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB layers: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB layers: {error_msg}"})
    
    # Get the layers data
    layers_data = response.get("result", [])
    
    if not layers_data:
        logger.info("No PCB layers found")
        return json.dumps({"message": "No PCB layers found in the current document"})
    
    logger.info(f"Retrieved PCB layers data")
    return json.dumps(layers_data, indent=2)

@mcp.tool()
async def set_pcb_layer_visibility(ctx: Context, layer_names: list, visible: bool) -> str:
    """
    Set visibility for specified PCB layers
    
    Args:
        layer_names (list): List of layer names to modify (e.g., ["Top Layer", "Bottom Layer", "Mechanical 1"])
        visible (bool): Whether to show (True) or hide (False) the specified layers
        
    Returns:
        str: JSON object with the result of the operation
    """
    logger.info(f"Setting layers visibility: {layer_names} to {visible}")
    
    # Execute the command in Altium to set layer visibility
    response = await altium_bridge.execute_command(
        "set_pcb_layer_visibility",
        {
            "layer_names": layer_names,
            "visible": visible
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error setting layer visibility: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to set layer visibility: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Layer visibility set successfully")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_component_data(ctx: Context, cmp_designators: list) -> str:
    """
    Get all data for components in Altium
    
    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])
    
    Returns:
        str: JSON object with all component data for requested designators
    """
    logger.info(f"Getting data for components: {cmp_designators}")
    
    # Execute the command in Altium to get all component data
    response = await altium_bridge.execute_command(
        "get_all_component_data",
        {}  # No parameters needed for this command in the Altium script
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    component_data = response.get("result", [])
    
    if not component_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        
        # Filter components by designator
        components = []
        missing_designators = []
        
        for designator in cmp_designators:
            found = False
            for component in component_list:
                if component.get("designator") == designator:
                    components.append(component)
                    found = True
                    break
            
            if not found:
                missing_designators.append(designator)
        
        result = {
            "components": components,
        }
        
        if missing_designators:
            result["missing_designators"] = missing_designators
            logger.info(f"Some designators not found: {missing_designators}")
        
        logger.info(f"Found data for {len(components)} components")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_selected_components_coordinates(ctx: Context) -> str:
    """
    Get coordinates and positioning information for selected components in Altium layout
    
    Returns:
        str: JSON array with positioning data (designator, x, y, rotation, width, height)
    """
    logger.info("Getting coordinates for selected components")
    
    # Execute the command in Altium to get selected components coordinates
    response = await altium_bridge.execute_command(
        "get_selected_components_coordinates",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting selected components coordinates: {error_msg}")
        return json.dumps({"error": f"Failed to get selected components coordinates: {error_msg}"})
    
    # Get the components coordinates data
    components_coords = response.get("result", [])
    
    if not components_coords:
        logger.info("No selected components found")
        return json.dumps({"message": "No components are currently selected in the layout"})
    
    logger.info(f"Retrieved positioning data for selected components")
    return json.dumps(components_coords, indent=2)

@mcp.tool()
async def get_all_designators(ctx: Context) -> str:
    """
    Get all component designators from the current Altium board
    
    Returns:
        str: JSON array of all component designators on the current board
    """
    logger.info("Getting all component designators")
    
    # Execute the command in Altium to get all component data
    response = await altium_bridge.execute_command(
        "get_all_component_data",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    component_data = response.get("result", [])
    
    if not component_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        
        # Extract designators
        designators = [comp.get("designator") for comp in component_list if "designator" in comp]
        
        logger.info(f"Found {len(designators)} designators")
        return json.dumps(designators)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_component_pins(ctx: Context, cmp_designators: list) -> str:
    """
    Get pin data for components in Altium

    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])

    Returns:
        str: JSON array, one entry per component with its placement info
             (x/y in mils relative to the board origin, rotation in degrees
             counterclockwise, layer) and a "pins" list. Per pin:
             - x/y: absolute pad position (mils, relative to board origin)
             - dx/dy: pad offset from the component origin in the footprint's
               rotation-0 frame. To predict a pad position for a planned
               placement: mirror dx (dx = -dx) if placing on the bottom layer,
               rotate (dx, dy) counterclockwise by the planned rotation, then
               add the planned component x/y.
             - rotation: the pad's own rotation (NOT the component rotation)
             - net, layer, width, height, shape
    """
    logger.info(f"Getting pin data for components: {cmp_designators}")
    
    # Execute the command in Altium to get pin data
    response = await altium_bridge.execute_command(
        "get_component_pins",
        {"designators": cmp_designators}  # Pass the list of designators
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting pin data: {error_msg}")
        return json.dumps({"error": f"Failed to get pin data: {error_msg}"})
    
    # Get the components pins data
    pins_data = response.get("result", [])
    
    if not pins_data:
        logger.info(f"No pin data found for designators: {cmp_designators}")
        return json.dumps({"message": "No pin data found for the specified components"})
    
    logger.info(f"Retrieved pin data for components")
    return json.dumps(pins_data, indent=2)

SANDBOX_DIR = MCP_DIR / "SandboxScript"
SANDBOX_PAS = SANDBOX_DIR / "Sandbox.pas"
SANDBOX_PRJ = SANDBOX_DIR / "Sandbox.PrjScr"
SANDBOX_LOG = EXCHANGE_DIR / "sandbox_log.txt"
SANDBOX_RESULT = EXCHANGE_DIR / "sandbox_result.json"
SANDBOX_BEGIN = "// === BEGIN EXPERIMENT"
SANDBOX_END = "// === END EXPERIMENT"
SANDBOX_DECL_BEGIN = "// === BEGIN DECLARATIONS"
SANDBOX_DECL_END = "// === END DECLARATIONS"


def _inject_between(src: str, begin: str, end: str, text: str, indent: str) -> str:
    """Replace the block between two marker lines, keeping both markers."""
    pre, rest = src.split(begin, 1)
    marker_line, rest = rest.split("\n", 1)
    _, post = rest.split(end, 1)
    body = "\n".join(indent + ln if ln.strip() else ln
                     for ln in text.strip("\n").splitlines())
    return pre + begin + marker_line + "\n" + body + "\n" + indent + end + post


def _dismiss_altium_dialogs():
    """Close Altium modal popups that would otherwise block a script run.

    Altium uses two kinds: Win32 task dialogs (#32770) and Delphi TMessageForm
    error/warning boxes. Only windows of the Altium process are touched - a
    dialog box of another application on the same desktop (an Explorer
    confirmation, a control-panel message) is never the script's problem and
    must not be answered on the user's behalf. Returns the text of each
    dialog closed (title plus its static controls), because a compile error
    or "another instance is busy" message is the only clue the script run
    leaves behind.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []
    user32 = ctypes.windll.user32
    found = []

    def window_text(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def process_id(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    altium_pids = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def find_altium(hwnd, lparam):
        if user32.IsWindowVisible(hwnd) and "Altium Designer" in window_text(hwnd):
            altium_pids.add(process_id(hwnd))
        return True

    user32.EnumWindows(find_altium, 0)
    if not altium_pids:
        return []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd) or process_id(hwnd) not in altium_pids:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770":
            found.append(hwnd)
        elif cls.value == "TMessageForm":
            if window_text(hwnd) in ("Error", "Warning", "Information", "Confirm"):
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    texts = []
    for h in found:
        parts = [window_text(h)]

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def child_cb(child, lparam):
            t = window_text(child).strip()
            if t and t not in ("OK", "Cancel", "Yes", "No"):
                parts.append(t)
            return True

        user32.EnumChildWindows(h, child_cb, 0)
        texts.append(" | ".join(p for p in parts if p))
        user32.PostMessageW(h, 0x0010, 0, 0)
    return texts


@mcp.tool()
async def run_altium_script(ctx: Context, script: str, timeout_seconds: int = 120,
                            declarations: str = "", declarations_file: str = "") -> str:
    """
    Run a DelphiScript snippet inside an isolated Altium sandbox and report
    what happened, step by step.

    Use this to develop and verify Altium API code before relying on it.
    Altium has no headless test mode and its failure modes are hostile: a
    runtime error leaves the script PAUSED IN THE DEBUGGER with no dialog,
    after which every later script run silently does nothing until the
    debugger is stopped (Ctrl+F3) or Altium is restarted. This tool detects
    that state and reports exactly which statement died.

    The script runs in a SEPARATE script project, so a crash here can never
    break the other MCP tools.

    Writing the script:
    - Call SandboxLog('...') before each risky statement. The log is flushed
      after every call, so the last logged line identifies what failed.
    - Assign findings to the string variable ResultText - it is returned.
    - DelphiScript has NO inline variable declarations. Reuse the provided
      scratch variables: S1..S3 (String), I1..I3 and B1 (Integer),
      Obj1..Obj5 (IDispatch), List1 (TStringList), IntMan, DbDoc - or pass
      your own const/var blocks, procedures and functions in `declarations`;
      they are placed at unit level before Run, so the body can call them
      and they can call SandboxLog. Names must not collide with the
      sandbox's own (LogLines, LogPath, OutPath, S1.., Obj1.., List1, ...).
    - try/except does NOT catch runtime errors such as bad conversions or
      invalid API calls, so it cannot be relied on to keep a script alive.
    - The sandbox is standalone: helpers and constants from the production
      units (TrimJSON, AddJSONProperty, ...) are NOT available; REPLACEALL is.
    - Never register objects into a library document and never write to shared
      or network library paths. Verify the target document kind first
      (ObjectID 32 = schematic, 33 = symbol library).

    For API guidance - interfaces, object models, worked examples - use the
    "altium-script" skill. ensure_altium_script_skill reports whether that
    skill is installed and can install it.

    Args:
        script (str): DelphiScript statements to execute (body only).
        timeout_seconds (int): How long to wait for completion (default 120).
        declarations (str): Optional unit-level DelphiScript (const, var,
            procedures, functions) made available to the body.
        declarations_file (str): Path to a .pas file whose contents are used
            as the declarations - for generated or multi-kilobyte helper
            units that would be unwieldy inline. Ignored if `declarations`
            is given.

    Returns:
        str: JSON with success, the step log, the script's ResultText, and on
             failure the last step reached plus whether Altium's script
             executor is now wedged and needs recovery.
    """
    logger.info(f"run_altium_script: {len(script.splitlines())} lines")

    if not SANDBOX_PAS.exists() or not SANDBOX_PRJ.exists():
        return json.dumps({"success": False,
                           "error": f"sandbox project missing at {SANDBOX_DIR}"})

    if not declarations and declarations_file:
        try:
            declarations = Path(declarations_file).read_text(encoding="utf-8")
        except OSError as e:
            return json.dumps({"success": False,
                               "error": f"could not read declarations_file: {e}"})

    try:
        src = SANDBOX_PAS.read_text(encoding="utf-8")
        src = _inject_between(src, SANDBOX_DECL_BEGIN, SANDBOX_DECL_END, declarations, "")
        src = _inject_between(src, SANDBOX_BEGIN, SANDBOX_END, script, "        ")
        SANDBOX_PAS.write_text(src, encoding="utf-8")
    except Exception as e:
        return json.dumps({"success": False, "error": f"could not inject script: {e}"})

    for f in (SANDBOX_LOG, SANDBOX_RESULT):
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass

    cmd = (f'"{altium_bridge.config.altium_exe_path}" -RScriptingSystem:RunScript('
           f'ProjectName="{SANDBOX_PRJ}"^|ProcName="Sandbox>Run")')
    subprocess.Popen(cmd, shell=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    start = time.time()
    dialogs = []
    while not SANDBOX_RESULT.exists() and time.time() - start < timeout_seconds:
        await asyncio.sleep(0.5)
        if time.time() - start > 6:
            dialogs += _dismiss_altium_dialogs()

    steps = []
    if SANDBOX_LOG.exists():
        steps = SANDBOX_LOG.read_text(encoding="utf-8", errors="replace").splitlines()

    if SANDBOX_RESULT.exists():
        result_text = SANDBOX_RESULT.read_text(encoding="utf-8", errors="replace").strip()
        return json.dumps({"success": True, "result": result_text, "steps": steps,
                           "dialogs_dismissed": len(dialogs), "dialogs": dialogs}, indent=2)

    if steps:
        return json.dumps({
            "success": False,
            "error": "script started but did not finish",
            "last_step_reached": steps[-1],
            "diagnosis": "The statement AFTER the last step is what crashed or paused the script.",
            "executor_wedged": True,
            "recovery": "Altium's script executor is now blocked. Recover by running this "
                        "shell command (sends the debugger Stop process to the running "
                        "Altium): \"<altium_exe>\" -REditScript:Stop  -- then retry.",
            "steps": steps,
            "dialogs_dismissed": len(dialogs), "dialogs": dialogs}, indent=2)

    return json.dumps({
        "success": False,
        "error": "script never started",
        "diagnosis": "Usually a COMPILE error in the script, or a previously paused "
                     "script blocking execution.",
        "executor_wedged": True,
        "recovery": "A previously paused script may be blocking execution. Recover by "
                    "running this shell command: \"<altium_exe>\" -REditScript:Stop  "
                    "-- then retry. If it still fails, the script itself has a COMPILE error.",
        "dialogs_dismissed": len(dialogs), "dialogs": dialogs}, indent=2)


@mcp.tool()
async def recover_script_executor(ctx: Context, timeout_seconds: int = 60) -> str:
    """
    Unwedge Altium's script executor after a script died, then verify.

    A runtime error leaves the failed script paused in Altium's debugger
    with no dialog, and every later RunScript silently does nothing until
    the debugger is stopped. This tool dispatches Altium's own Stop
    Debugging process into the running instance (`X2.EXE -REditScript:Stop`,
    the same launch channel the other tools use, so no window focus or
    keystrokes are needed) and then runs a one-line probe through the
    sandbox. Only a probe that comes back proves the executor is usable
    again; a wedge is defined by scripts silently doing nothing.

    Args:
        timeout_seconds (int): How long to wait for the probe (default 60).

    Returns:
        str: JSON with recovered (bool), the probe result and the steps.
    """
    exe = altium_bridge.config.altium_exe_path
    if not os.path.exists(exe):
        return json.dumps({"recovered": False, "error": f"Altium executable not found: {exe}"})
    logger.info("recover_script_executor: dispatching EditScript:Stop")
    subprocess.Popen(f'"{exe}" -REditScript:Stop', shell=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(3)
    probe = await run_altium_script(ctx, "ResultText := '{\"probe\": \"executor ok\"}';",
                                    timeout_seconds)
    try:
        outcome = json.loads(probe)
    except ValueError:
        outcome = {"success": False, "raw": probe}
    return json.dumps({"recovered": bool(outcome.get("success")), "probe": outcome}, indent=2)


@mcp.tool()
async def ensure_altium_script_skill(ctx: Context, install: bool = False) -> str:
    """
    Check whether the "altium-script" skill is installed, and optionally
    install it.

    That skill documents the Altium DelphiScript API - interfaces, object
    models, worked examples, conventions, and how to discover undocumented
    processes - and is the reference to consult before writing scripts for
    run_altium_script or debugging Altium API calls.

    Source: https://github.com/coffeenmusic/altium-scripts-skill

    Installing writes into the user's skills directory, so it only happens when
    install=True is passed explicitly. Skills load at client startup, so a
    newly installed skill becomes available after restarting the client.

    Args:
        install (bool): Install the skill if missing (requires git on PATH).

    Returns:
        str: JSON with installed (bool), the path checked, and the next step.
    """
    skill_dir = Path.home() / ".claude" / "skills" / "altium-script"
    repo = "https://github.com/coffeenmusic/altium-scripts-skill"

    if (skill_dir / "SKILL.md").exists():
        return json.dumps({"installed": True, "path": str(skill_dir),
                           "note": "Use the altium-script skill for API guidance."},
                          indent=2)

    if not install:
        return json.dumps({
            "installed": False,
            "path": str(skill_dir),
            "source": repo,
            "next_step": "Call again with install=true to clone it, or install manually "
                         "into the path above."}, indent=2)

    try:
        skill_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["git", "clone", "--depth", "1", repo, str(skill_dir)],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return json.dumps({
                "installed": False, "path": str(skill_dir),
                "error": (proc.stderr or proc.stdout)[:400],
                "hint": f"Requires git on PATH; otherwise download {repo} and extract "
                        "to the path above."}, indent=2)
        return json.dumps({"installed": True, "path": str(skill_dir), "source": repo,
                           "next_step": "Restart the client so the skill is loaded."},
                          indent=2)
    except Exception as e:
        return json.dumps({"installed": False, "path": str(skill_dir),
                           "error": str(e)[:300]}, indent=2)


@mcp.tool()
async def get_footprint_primitives(ctx: Context, library_path: str = "", footprint_name: str = "") -> str:
    """
    Read the primitives of footprints in a PCB library (.PcbLib).

    Modes:
    - footprint_name omitted: inventory of every footprint with per-type
      primitive counts (pads, tracks, arcs, fills, texts, regions, vias,
      component_bodies)
    - footprint_name given (exact, case-insensitive): full geometry dump -
      pads (position, rotation, layer, sizes/shape per stack, hole size/
      type/width/rotation, plating), tracks, arcs, fills, texts, regions
      (outline vertices). Coordinates in mils; shapes and hole types as raw
      Altium enum ints; layers as names. 3D component bodies are models,
      not 2D primitives, and are excluded.
    - footprint_name "*": full dump of every footprint

    Use as the reference when recreating or validating footprints, and to
    survey what a library requires.

    Args:
        library_path (str, optional): Full path to the .PcbLib. Omit to use
            the currently focused PCB library (an already-open library is
            only focused, never reloaded).
        footprint_name (str, optional): Exact footprint name, or "*".

    Returns:
        str: JSON - inventory: {library_name, footprint_count, footprints:
             [{name, description, <type counts>}]}; dump: primitives list
             per footprint
    """
    logger.info(f"Getting footprint primitives (library={library_path}, footprint={footprint_name})")

    response = await altium_bridge.execute_command(
        "get_footprint_primitives",
        {"library_path": library_path, "footprint_name": footprint_name}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        return json.dumps({"success": False, "error": f"Failed to get footprint primitives: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def create_footprints_batch(ctx: Context, spec_file: str) -> str:
    """
    Create many PCB footprints in a single Altium script run.

    The batch equivalent of create_pcb_footprint with far broader coverage:
    through-hole and SMD pads (full pad stack, holes, slots, plating,
    rotation), tracks, arcs, fills, texts, and regions on any layer.
    Verified by exact round-trips of complete production footprint
    libraries. Prefer this for bulk imports/migrations; use
    get_footprint_primitives on an existing footprint to learn the exact
    field conventions.

    Args:
        spec_file (str): Path to a plain-text spec file, one record per line
            (coords in mils, layers as names, shapes/hole types as raw
            Altium enum ints, booleans as 1/0):
            FPLIB|<path to .PcbLib>   (optional first line: opens/focuses)
            FOOTPRINT|<name>|<description>
            PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape]]
            TRACK|x1|y1|x2|y2|width|layer
            ARC|cx|cy|radius|start_angle|end_angle|width|layer
            FILL|x1|y1|x2|y2|rotation|layer
            TEXT|x|y|size|width|rotation|layer|mirror|ttf|text
            REGION|layer|kind|x1|y1|x2|y2|...

    Returns:
        str: JSON with created count, primitive_errors, failed names
    """
    logger.info(f"Creating footprints batch from {spec_file}")

    response = await altium_bridge.execute_command(
        "create_footprints_batch",
        {"spec_file": spec_file}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        return json.dumps({"success": False, "error": f"Failed batch footprint creation: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def build_schematic(ctx: Context, parts: list, wires: list = None,
                          junctions: list = None, net_labels: list = None,
                          power_ports: list = None, notes: list = None) -> str:
    """
    Build a wired schematic on a NEW sheet from a circuit description.

    READ dev/SCHEMATIC_CONVENTIONS.md BEFORE CALLING. This tool places exactly
    what it is given; it does not lay out or correct spacing. The conventions
    file records the drafting rules that make the result readable, each one
    learned by getting it wrong. The ones that most often produce a plausible
    but wrong sheet:

      * A pin's connection point is NOT Pin.Location - it is PinLength further
        along the pin. Do not guess coordinates. Call with parts only first,
        read the returned pin_map, then call again with wires routed from those
        measured coordinates.
      * Never run a riser in a pin's connection column; it drives through every
        pin sharing that column.
      * Give a shunt part one grid of wire between its pin and the node it taps,
        rather than putting the junction on the pin.
      * A net label that does not TOUCH its wire names nothing.
      * A shunt part occupies the band below its rail. Do not route another net
        through that band - the wire will pass through the symbol body.
      * A ground port's "GND" label is always drawn (ShowNetName cannot hide it)
        and occupies ~300 mil below the port. Keep wires out of that band.

    Args:
        parts: component dicts. Required keys: designator, symbol_library,
            symbol, x, y (mils). Optional: design_item_id, orientation (0-3),
            mirror (0/1), comment, description, footprint,
            parameters ({name: value}).
        wires: each a flat list of alternating x,y in mils, e.g.
            [3900, 3000, 5300, 3000, 5300, 4900] - two segments, three points.
        junctions: [{"x":..,"y":..}] - only where 3+ branches actually meet.
        net_labels: [{"x":..,"y":..,"orientation":0,"text":"LED+"}] - the
            coordinate must lie on a wire.
        power_ports: [{"x":..,"y":..,"orientation":3,"style":5,"text":"GND",
            "show_net_name":false}]. Harvest style/orientation from an existing
            sheet rather than guessing: 5 = digital ground, 2 = supply bar.
        notes: [{"x":..,"y":..,"text":".."}] free text.

    Returns:
        JSON with the created sheet name, counts, and the pin map - every
        placed pin's true connection point, for routing a follow-up call.
    """
    logger.info(f"Building schematic: {len(parts)} parts")

    lines = []
    for p in parts:
        for key in ("designator", "symbol_library", "symbol", "x", "y"):
            if key not in p:
                return json.dumps({"success": False,
                                   "error": f"part missing required key '{key}': {p}"})
        lines.append("PART|{}|{}|{}|{}|{}|{}|{}|{}".format(
            p["designator"], p["symbol_library"], p["symbol"],
            p.get("design_item_id", ""), int(p["x"]), int(p["y"]),
            int(p.get("orientation", 0)), 1 if p.get("mirror") else 0))
        if p.get("comment"):
            lines.append(f"COMMENT|{p['comment']}")
        if p.get("footprint"):
            lines.append(f"FOOTPRINT|{p['footprint']}")
        if p.get("description"):
            lines.append(f"DESCRIPTION|{p['description']}")
        for name, val in (p.get("parameters") or {}).items():
            lines.append(f"PARAM|{name}|{val}")

    for route in (wires or []):
        if len(route) < 4 or len(route) % 2:
            return json.dumps({"success": False,
                               "error": f"wire needs an even count of >=4 coords: {route}"})
        lines.append("WIRE|" + "|".join(str(int(v)) for v in route))
    for j in (junctions or []):
        lines.append(f"JUNCTION|{int(j['x'])}|{int(j['y'])}")
    for n in (net_labels or []):
        lines.append("NETLABEL|{}|{}|{}|{}".format(
            int(n["x"]), int(n["y"]), int(n.get("orientation", 0)), n["text"]))
    for pw in (power_ports or []):
        lines.append("POWER|{}|{}|{}|{}|{}|{}".format(
            int(pw["x"]), int(pw["y"]), int(pw.get("orientation", 3)),
            int(pw.get("style", 5)), pw["text"],
            1 if pw.get("show_net_name") else 0))
    for nt in (notes or []):
        lines.append(f"NOTE|{int(nt['x'])}|{int(nt['y'])}|{nt['text']}")

    spec_path = Path("C:/Users/Public/altium_mcp/circuit_spec.txt")
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    # cp1252: the bridge reads these as ANSI, and part descriptions carry
    # characters that are not plain ASCII
    spec_path.write_text("\n".join(lines) + "\n", encoding="cp1252", errors="replace")

    response = await altium_bridge.execute_command("build_circuit", {})
    if not response.get("success", False):
        return json.dumps({"success": False,
                           "error": response.get("error", "unknown error")})

    result = response.get("result", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return result

    # Hand back the measured pin map so the caller can route a second pass
    pin_map = {}
    pm = Path("C:/Users/Public/altium_mcp/pin_map.txt")
    if pm.is_file():
        for line in pm.read_text(errors="replace").splitlines():
            f = line.strip().split("|")
            if len(f) == 5 and f[0] == "PIN":
                pin_map.setdefault(f[1], {})[f[2]] = [int(f[3]), int(f[4])]
    result["pin_map"] = pin_map
    result["pin_map_note"] = ("Absolute electrical connection points. Route "
                              "wires from these, not from predicted offsets.")
    return json.dumps(result, indent=2)

# ---------------------------------------------------------------------------
# Editing existing schematic sheets by data (schematic_edit.pas)
# ---------------------------------------------------------------------------

SHEET_SPEC = EXCHANGE_DIR / "sheet_edit_spec.txt"
SHEET_OBJECTS = EXCHANGE_DIR / "sheet_objects.json"
COMPILE_REPORT = EXCHANGE_DIR / "compile_report.json"
PROJECT_REPORT = EXCHANGE_DIR / "project_report.json"
MULTIBOARD_TEMPLATE = Path(__file__).resolve().parent / "resources" / "multiboard_schematic"
PIN_MAP = EXCHANGE_DIR / "pin_map.txt"
# The bridge reads spec files as ANSI text, so they are written in the
# Windows code page of this machine - that is what keeps non-ASCII labels
# (Cyrillic notes, degree signs, Greek letters) intact end to end.
SPEC_ENCODING = locale.getpreferredencoding(False)


def _spec_field(value) -> str:
    """One pipe-delimited field: the separator itself cannot appear in a value."""
    return str(value).replace("|", "/").replace("\r", " ").replace("\n", " ")


def _read_pin_map() -> dict:
    pin_map = {}
    if PIN_MAP.is_file():
        for line in PIN_MAP.read_text(encoding=SPEC_ENCODING, errors="replace").splitlines():
            f = line.strip().split("|")
            if len(f) == 5 and f[0] == "PIN":
                pin_map.setdefault(f[1], {})[f[2]] = [int(f[3]), int(f[4])]
    return pin_map


async def _run_sheet_spec(lines: list) -> str:
    SHEET_SPEC.write_text("\n".join(lines) + "\n", encoding=SPEC_ENCODING, errors="replace")
    if PIN_MAP.exists():
        PIN_MAP.unlink()
    response = await altium_bridge.execute_command("edit_schematic_sheet", {})
    if not response.get("success", False):
        return json.dumps({"success": False, "error": response.get("error", "unknown error")})
    result = response.get("result", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return result
    pin_map = _read_pin_map()
    if pin_map:
        result["pin_map"] = pin_map
        result["pin_map_note"] = "Absolute connection points (mils) of the placed pins; route wires from these."
    return json.dumps(result, indent=2, ensure_ascii=False)


def _part_records(parts: list) -> list:
    lines = []
    for p in parts:
        for key in ("designator", "symbol", "x", "y"):
            if key not in p:
                raise ValueError(f"part missing required key '{key}': {p}")
        lines.append("PART|{}|{}|{}|{}|{}|{}".format(
            _spec_field(p["designator"]), _spec_field(p["symbol"]), int(p["x"]), int(p["y"]),
            int(p.get("orientation", 0)), 1 if p.get("mirror") else 0))
        if p.get("comment") is not None:
            lines.append(f"COMMENT|{_spec_field(p['comment'])}")
        if p.get("comment_hidden"):
            lines.append("COMMENT_HIDDEN|1")
        if p.get("description"):
            lines.append(f"DESCRIPTION|{_spec_field(p['description'])}")
        if p.get("footprint"):
            lines.append(f"FOOTPRINT|{_spec_field(p['footprint'])}")
        for name, val in (p.get("parameters") or {}).items():
            lines.append(f"PARAM|{_spec_field(name)}|{_spec_field(val)}")
        if p.get("designator_at"):
            lines.append("DESIGNATOR_AT|{}|{}".format(int(p["designator_at"][0]), int(p["designator_at"][1])))
        if p.get("comment_at"):
            lines.append("COMMENT_AT|{}|{}".format(int(p["comment_at"][0]), int(p["comment_at"][1])))
    return lines


def _wiring_records(wires, junctions, net_labels, power_ports, ports, no_erc, notes, graphic_lines=None) -> list:
    lines = []
    for route in (wires or []):
        if len(route) < 4 or len(route) % 2:
            raise ValueError(f"wire needs an even count of >=4 coords: {route}")
        lines.append("WIRE|" + "|".join(str(int(v)) for v in route))
    for j in (junctions or []):
        lines.append(f"JUNCTION|{int(j['x'])}|{int(j['y'])}")
    for n in (net_labels or []):
        lines.append("NETLABEL|{}|{}|{}|{}".format(int(n["x"]), int(n["y"]), int(n.get("orientation", 0)), _spec_field(n["text"])))
    for pw in (power_ports or []):
        lines.append("POWER|{}|{}|{}|{}|{}|{}".format(
            int(pw["x"]), int(pw["y"]), int(pw.get("orientation", 3)), int(pw.get("style", 5)),
            _spec_field(pw["text"]), 1 if pw.get("show_net_name") else 0))
    for pt in (ports or []):
        lines.append("SPORT|{}|{}|{}|{}|{}|{}".format(
            int(pt["x"]), int(pt["y"]), _spec_field(pt["name"]), int(pt.get("io_type", 0)),
            int(pt.get("style", 0)), int(pt.get("width", 1000))))
    for n in (no_erc or []):
        lines.append(f"NOERC|{int(n['x'])}|{int(n['y'])}")
    for nt in (notes or []):
        rec = "NOTE|{}|{}|{}".format(int(nt["x"]), int(nt["y"]), _spec_field(nt["text"]))
        if nt.get("font_size"):
            rec += "|{}|{}".format(int(nt["font_size"]), _spec_field(nt.get("font_name", "Arial")))
        lines.append(rec)
    for g in (graphic_lines or []):
        if len(g) != 4:
            raise ValueError(f"line needs x1, y1, x2, y2: {g}")
        lines.append("LINE|" + "|".join(str(int(v)) for v in g))
    return lines


def _sheet_symbol_records(symbols: list) -> list:
    lines = []
    for sym in symbols:
        for key in ("x", "y", "x_size", "y_size", "name", "file"):
            if key not in sym:
                raise ValueError(f"sheet symbol missing required key '{key}': {sym}")
        rec = "SHEETSYMBOL|{}|{}|{}|{}|{}|{}".format(
            int(sym["x"]), int(sym["y"]), int(sym["x_size"]), int(sym["y_size"]),
            _spec_field(sym["name"]), _spec_field(sym["file"]))
        if sym.get("area_color") is not None or sym.get("line_color") is not None:
            rec += "|{}|{}".format(
                "" if sym.get("area_color") is None else int(sym["area_color"]),
                "" if sym.get("line_color") is None else int(sym["line_color"]))
        lines.append(rec)
        for e in (sym.get("entries") or []):
            lines.append("SHEETENTRY|{}|{}|{}|{}".format(
                _spec_field(e["name"]), int(e.get("side", 0)), int(e["distance"]), int(e.get("io_type", 0))))
    return lines


@mcp.tool()
async def create_schematic_project(ctx: Context, project_path: str, sheets: list) -> str:
    """
    Create a .PrjPcb with new, sized, empty schematic sheets and save it.

    The project file is created when it does not exist and opened in Altium.
    Each sheet that does not exist yet is created, given a custom size (no
    border or title block - draw your own with add_sheet_symbols' lines and
    notes), saved under its path and added to the project; a sheet that
    already exists is only added. Fill the sheets afterwards with
    add_sheet_symbols, place_schematic_components and add_schematic_wiring.

    Args:
        project_path (str): Full path of the .PrjPcb.
        sheets (list): [{"path": "...SchDoc", "width": 16535, "height": 11693}]
            in mils; the defaults are an A3 landscape sheet.

    Returns:
        str: JSON with projects_created / sheets_created / documents_added /
             projects_saved and warnings.
    """
    logger.info(f"create_schematic_project {project_path} with {len(sheets)} sheets")
    lines = [f"PROJECT|{_spec_field(project_path)}"]
    for sheet in sheets:
        if "path" not in sheet:
            return json.dumps({"success": False, "error": f"sheet missing 'path': {sheet}"})
        if Path(sheet["path"]).is_file():
            lines.append(f"ADDTOPROJECT|{_spec_field(sheet['path'])}")
        else:
            lines.append("NEWSHEET|{}|{}|{}".format(
                _spec_field(sheet["path"]), int(sheet.get("width", 16535)), int(sheet.get("height", 11693))))
    lines.append("SAVEPROJECT")
    return await _run_sheet_spec(lines)


@mcp.tool()
async def add_sheet_symbols(ctx: Context, sheet_path: str, symbols: list, wires: list = None,
                            net_labels: list = None, notes: list = None, lines: list = None) -> str:
    """
    Place hierarchical sheet symbols with their sheet entries on an EXISTING
    sheet - the active block diagram of a project - and save it.

    Each symbol references a child .SchDoc; each entry is a hierarchical
    connection that Altium matches with the same-named port on that child
    sheet. Entries are stacked from the symbol's top edge: an entry on the
    left side of a symbol whose top-left corner is (x, y), at distance d,
    connects at (x, y - d); on the right side at (x + x_size, y - d). Wires
    from the entries, net labels on those wires, notes and frame lines are
    drawn in the same call.

    Args:
        sheet_path (str): Full path of the target .SchDoc.
        symbols (list): [{"x", "y", "x_size", "y_size", "name", "file",
            "entries": [{"name", "side", "distance", "io_type"}],
            "area_color", "line_color"}] - mils; side 0 left, 1 right, 2 top,
            3 bottom; io_type 0 unspecified, 1 output, 2 input, 3 bidirectional;
            the colors are optional BGR integers.
        wires, net_labels, notes: as in add_schematic_wiring.
        lines (list): [[x1, y1, x2, y2], ...] graphical lines, not wires.

    Returns:
        str: JSON with sheet_symbols / sheet_entries / wires / net_labels /
             notes / lines and warnings.
    """
    logger.info(f"add_sheet_symbols on {sheet_path}: {len(symbols)} symbols")
    spec = [f"SHEET|{sheet_path}"]
    try:
        spec += _sheet_symbol_records(symbols)
        spec += _wiring_records(wires, None, net_labels, None, None, None, notes, lines)
    except (ValueError, KeyError) as e:
        return json.dumps({"success": False, "error": str(e)})
    return await _run_sheet_spec(spec)


@mcp.tool()
async def edit_schematic_sheet(ctx: Context, spec_file: str) -> str:
    """
    Apply a pipe-delimited edit spec to one or more EXISTING schematic sheets.

    This is the batch form behind place_schematic_components,
    add_schematic_wiring, edit_schematic_text, set_component_parameters and
    delete_schematic_objects; use it directly for a mixed edit in one Altium
    run. Every touched sheet is saved. Coordinates in mils.

    Records (one per line; a value must not contain '|'):
        SHEET|<path .SchDoc>            target for the records that follow
        PROJECT|<path .PrjPcb>          open it (created first when missing)
        NEWSHEET|<path .SchDoc>|width|height   create, size, save a sheet
        ADDTOPROJECT|<path .SchDoc>  SAVEPROJECT
        LIBRARY|<path .SchLib>          symbol source for PART records
        PART|designator|symbol|x|y|orientation|mirror
        COMMENT|text  DESCRIPTION|text  FOOTPRINT|model  PARAM|name|value
        DESIGNATOR_AT|x|y  COMMENT_AT|x|y  COMMENT_HIDDEN|1   (last PART)
        WIRE|x|y|x|y[|x|y...]  JUNCTION|x|y  NOERC|x|y
        NETLABEL|x|y|orientation|text
        POWER|x|y|orientation|style|text|show_net_name
        SPORT|x|y|name|iotype|style|width
        NOTE|x|y|text[|font_size[|font_name]]
        TEXT_REPLACE|old|new  TEXT_MOVE|text|x|y  TEXT_DELETE|text
        SET_PARAM|designator|name|value  SET_COMMENT|designator|text
        SET_FOOTPRINT|designator|model
        DELETE_PART|designator  DELETE_AT|kind|x|y  (kind: wire netlabel label
                                                      noerc junction port power)
        SHEETSYMBOL_RENAME|old|new
        LINE|x1|y1|x2|y2                graphical line (not a wire)
        SHEETSYMBOL|x|y|x_size|y_size|name|file[|area_color|line_color]
        SHEETENTRY|name|side|distance|iotype   (last SHEETSYMBOL)
    A library component whose pins do not survive Altium's Replicate is
    rebuilt from its primitives; the result says so in `warnings`.

    Args:
        spec_file (str): Path to the spec file (text in the Windows code page).

    Returns:
        str: JSON with per-record counts, warnings, and the pin map of every
             placed part (absolute connection points).
    """
    logger.info(f"edit_schematic_sheet from {spec_file}")
    try:
        text = Path(spec_file).read_text(encoding=SPEC_ENCODING, errors="replace")
    except OSError as e:
        return json.dumps({"success": False, "error": f"could not read spec file: {e}"})
    return await _run_sheet_spec(text.splitlines())


@mcp.tool()
async def place_schematic_components(ctx: Context, sheet_path: str, library_path: str, parts: list) -> str:
    """
    Place symbols from a .SchLib onto an EXISTING schematic sheet and save it.

    Unlike build_schematic this does not create a new sheet: it adds parts to
    the sheet given, so hierarchical designs can be extended sheet by sheet.
    Read the returned pin_map and route wires from those measured points with
    add_schematic_wiring - a pin's connection point is PinLength away from
    Pin.Location, never guess it.

    Args:
        sheet_path (str): Full path of the target .SchDoc (opened if needed).
        library_path (str): Full path of the .SchLib holding the symbols.
        parts (list): dicts with designator, symbol (LibReference), x, y (mils);
            optional orientation (0-3), mirror (bool), comment, comment_hidden,
            description, footprint (PCBLIB model name), parameters {name: value},
            designator_at [x, y], comment_at [x, y].

    Returns:
        str: JSON with parts_placed, warnings and pin_map.
    """
    logger.info(f"place_schematic_components: {len(parts)} parts on {sheet_path}")
    try:
        lines = [f"SHEET|{sheet_path}", f"LIBRARY|{library_path}"] + _part_records(parts)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})
    return await _run_sheet_spec(lines)


@mcp.tool()
async def add_schematic_wiring(ctx: Context, sheet_path: str, wires: list = None, junctions: list = None,
                               net_labels: list = None, power_ports: list = None, ports: list = None,
                               no_erc: list = None, notes: list = None) -> str:
    """
    Add wires, junctions, net labels, power ports, sheet ports, No-ERC markers
    and free text to an EXISTING schematic sheet, then save it.

    Conventions that keep the result electrically right (see
    dev/SCHEMATIC_CONVENTIONS.md): a net label must sit ON a wire to name it;
    a wire end on a pin's connection point connects, a wire crossing a pin's
    body does not; junctions only where three or more branches meet.

    Args:
        sheet_path (str): Full path of the target .SchDoc.
        wires (list): each a flat list of alternating x, y in mils.
        junctions (list): [{"x", "y"}]
        net_labels (list): [{"x", "y", "text", "orientation"}]
        power_ports (list): [{"x", "y", "text", "orientation", "style", "show_net_name"}]
        ports (list): [{"x", "y", "name", "io_type", "style", "width"}]
        no_erc (list): [{"x", "y"}]
        notes (list): [{"x", "y", "text", "font_size", "font_name"}]

    Returns:
        str: JSON with counts per object kind and warnings.
    """
    logger.info(f"add_schematic_wiring on {sheet_path}")
    try:
        lines = [f"SHEET|{sheet_path}"] + _wiring_records(wires, junctions, net_labels, power_ports, ports, no_erc, notes)
    except (ValueError, KeyError) as e:
        return json.dumps({"success": False, "error": str(e)})
    return await _run_sheet_spec(lines)


@mcp.tool()
async def edit_schematic_text(ctx: Context, sheet_path: str, replace: list = None, move: list = None,
                              delete: list = None, add: list = None, rename_sheet_symbols: list = None) -> str:
    """
    Change free text (labels/notes) and sheet-symbol names on an EXISTING sheet.

    Matching is by exact text. Use get_schematic_sheet first to read the
    current labels with their coordinates. Operations apply in the order
    replace, move, delete, add, rename, so a text added in this call cannot
    also be deleted by it.

    Args:
        sheet_path (str): Full path of the target .SchDoc.
        replace (list): [{"old": "...", "new": "..."}]
        move (list): [{"text": "...", "x": .., "y": ..}]
        delete (list): ["text", ...]
        add (list): [{"x", "y", "text", "font_size", "font_name"}]
        rename_sheet_symbols (list): [{"old": "...", "new": "..."}]

    Returns:
        str: JSON with texts_replaced / texts_moved / texts_deleted / notes /
             sheet_symbols_renamed and warnings for texts not found.
    """
    logger.info(f"edit_schematic_text on {sheet_path}")
    lines = [f"SHEET|{sheet_path}"]
    for r in (replace or []):
        lines.append(f"TEXT_REPLACE|{_spec_field(r['old'])}|{_spec_field(r['new'])}")
    for m in (move or []):
        lines.append(f"TEXT_MOVE|{_spec_field(m['text'])}|{int(m['x'])}|{int(m['y'])}")
    for d in (delete or []):
        lines.append(f"TEXT_DELETE|{_spec_field(d)}")
    try:
        lines += _wiring_records(None, None, None, None, None, None, add)
    except (ValueError, KeyError) as e:
        return json.dumps({"success": False, "error": str(e)})
    for r in (rename_sheet_symbols or []):
        lines.append(f"SHEETSYMBOL_RENAME|{_spec_field(r['old'])}|{_spec_field(r['new'])}")
    return await _run_sheet_spec(lines)


@mcp.tool()
async def set_component_parameters(ctx: Context, sheet_path: str, components: list) -> str:
    """
    Set parameters, comment or footprint model of components already on a sheet.

    Typical use: assembly variants kept as a parameter (e.g. Assembly_Base =
    FIT / DNP), value changes, adding a footprint link.

    Args:
        sheet_path (str): Full path of the target .SchDoc.
        components (list): [{"designator": "R12", "parameters": {"Assembly_Base": "FIT"},
                             "comment": "0R", "footprint": "RES_0402"}] - each
            key other than designator is optional.

    Returns:
        str: JSON with parts_updated and warnings for designators not found.
    """
    logger.info(f"set_component_parameters on {sheet_path}")
    lines = [f"SHEET|{sheet_path}"]
    for c in components:
        d = _spec_field(c["designator"])
        for name, val in (c.get("parameters") or {}).items():
            lines.append(f"SET_PARAM|{d}|{_spec_field(name)}|{_spec_field(val)}")
        if c.get("comment") is not None:
            lines.append(f"SET_COMMENT|{d}|{_spec_field(c['comment'])}")
        if c.get("footprint"):
            lines.append(f"SET_FOOTPRINT|{d}|{_spec_field(c['footprint'])}")
    return await _run_sheet_spec(lines)


@mcp.tool()
async def delete_schematic_objects(ctx: Context, sheet_path: str, designators: list = None, at: list = None) -> str:
    """
    Remove components by designator and other objects by position from a sheet.

    Args:
        sheet_path (str): Full path of the target .SchDoc.
        designators (list): components to remove, e.g. ["TP01", "R99"].
        at (list): [{"kind": "wire", "x": .., "y": ..}] - kind is one of wire
            (matched on its first vertex), netlabel, label, noerc, junction,
            port, power; coordinates in mils as get_schematic_sheet reports them.

    Returns:
        str: JSON with parts_deleted / objects_deleted and warnings.
    """
    logger.info(f"delete_schematic_objects on {sheet_path}")
    lines = [f"SHEET|{sheet_path}"]
    for d in (designators or []):
        lines.append(f"DELETE_PART|{_spec_field(d)}")
    for a in (at or []):
        lines.append(f"DELETE_AT|{_spec_field(a['kind'])}|{int(a['x'])}|{int(a['y'])}")
    return await _run_sheet_spec(lines)


@mcp.tool()
async def get_schematic_sheet(ctx: Context, sheet_path: str) -> str:
    """
    Read every object of a schematic sheet: components with parameters,
    footprint models and pin connection points, labels, net labels, ports,
    sheet symbols with their entries, wires, junctions, No-ERC and power ports.

    The sheet is opened if it is not already. Coordinates in mils. Large
    sheets are returned in full; the same JSON is also left in the exchange
    folder (`file` in the result).

    Args:
        sheet_path (str): Full path of the .SchDoc.

    Returns:
        str: JSON object with sheet size and the object arrays.
    """
    logger.info(f"get_schematic_sheet {sheet_path}")
    if SHEET_OBJECTS.exists():
        SHEET_OBJECTS.unlink()
    response = await altium_bridge.execute_command("get_schematic_sheet", {"sheet_path": sheet_path})
    if not response.get("success", False):
        return json.dumps({"success": False, "error": response.get("error", "unknown error")})
    if not SHEET_OBJECTS.is_file():
        return json.dumps({"success": False, "error": "sheet dump file was not written", "result": response.get("result")})
    try:
        data = json.loads(SHEET_OBJECTS.read_text(encoding=SPEC_ENCODING, errors="replace"))
    except ValueError as e:
        return json.dumps({"success": False, "error": f"sheet dump is not valid JSON: {e}"})
    data["file"] = str(SHEET_OBJECTS)
    return json.dumps(data, indent=1, ensure_ascii=False)


@mcp.tool()
async def compile_project(ctx: Context, project_path: str, include_pins: bool = False) -> str:
    """
    Compile a project and report its violations (ERC) and, optionally, the net
    of every pin in the flattened design.

    The violation list is Altium's own: level, kind ("Nets with no driving
    source", ...), detail with the pins involved. include_pins gives the
    verified netlist - the check that proves a wire or net label actually
    connected what it was meant to.

    Args:
        project_path (str): Full path of the .PrjPcb (opened if needed).
        include_pins (bool): Also list designator / pin / net for every pin.

    Returns:
        str: JSON with compiled, document counts, violations[] and pins[].
    """
    logger.info(f"compile_project {project_path}")
    if COMPILE_REPORT.exists():
        COMPILE_REPORT.unlink()
    response = await altium_bridge.execute_command(
        "compile_project", {"project_path": project_path, "include_pins": "true" if include_pins else "false"})
    if not response.get("success", False):
        return json.dumps({"success": False, "error": response.get("error", "unknown error")})
    if not COMPILE_REPORT.is_file():
        return json.dumps({"success": False, "error": "compile report was not written", "result": response.get("result")})
    try:
        data = json.loads(COMPILE_REPORT.read_text(encoding=SPEC_ENCODING, errors="replace"))
    except ValueError as e:
        return json.dumps({"success": False, "error": f"compile report is not valid JSON: {e}"})
    return json.dumps(data, indent=1, ensure_ascii=False)


@mcp.tool()
async def open_project(ctx: Context, project_path: str) -> str:
    """
    Open a project of any kind (.PrjPcb, .PrjMbd, ...) in Altium and list its
    logical documents with their kinds and whether each file exists.

    Use it to check a project that a tool wrote, or to bring a project into
    the workspace before sheet tools work on its documents.

    Args:
        project_path (str): Full path of the project file.

    Returns:
        str: JSON with project, kind, logical_documents and documents[].
    """
    logger.info(f"open_project {project_path}")
    if PROJECT_REPORT.exists():
        PROJECT_REPORT.unlink()
    response = await altium_bridge.execute_command("open_project", {"project_path": project_path})
    if not response.get("success", False):
        return json.dumps({"success": False, "error": response.get("error", "unknown error")})
    if not PROJECT_REPORT.is_file():
        return json.dumps({"success": False, "error": "project report was not written", "result": response.get("result")})
    try:
        data = json.loads(PROJECT_REPORT.read_text(encoding=SPEC_ENCODING, errors="replace"))
    except ValueError as e:
        return json.dumps({"success": False, "error": f"project report is not valid JSON: {e}"})
    return json.dumps(data, indent=1, ensure_ascii=False)


def _project_ini(document_paths: list) -> str:
    """Minimal project file: the keys the hierarchy depends on plus the document
    list. Altium fills in the rest when it saves the project."""
    lines = ["[Design]", "Version=1.0", "HierarchyMode=0", "AllowPortNetNames=0",
             "AllowSheetEntryNetNames=1", "NetlistSinglePinNets=1", ""]
    for n, path in enumerate(document_paths, 1):
        lines += [f"[Document{n}]", f"DocumentPath={path}", ""]
    return "\r\n".join(lines)


def _relative_windows_path(path: str, base_dir: str) -> str:
    try:
        return os.path.relpath(path, base_dir).replace("/", "\\")
    except ValueError:  # different drive or share
        return path


@mcp.tool()
async def create_multiboard_project(ctx: Context, project_path: str, modules: list,
                                    schematic_path: str = "", documents: list = None,
                                    sheet_size: str = "A3") -> str:
    """
    Create a Multi-board Design project (.PrjMbd) with a Multi-board Schematic
    (.MbsDoc) whose modules reference child PCB projects.

    The schematic editor has no scripting interface, so the files are written
    directly: the project is an INI file, the schematic a ZIP of JSON files
    built from the template of an empty document saved by Altium Designer 26.
    Each module gets its designator, title and source project. Bringing in
    the connectors of the child projects (components with a parameter
    System = Connector) and connecting them is link_multiboard_modules;
    open_project lets Altium load the result.

    Args:
        project_path (str): Full path of the .PrjMbd to create (must not exist).
        modules (list): [{"designator": "M1", "title": "Sensor board",
            "project": "<full path .PrjPcb>", "board": "<full path .PcbDoc>",
            "x", "y", "width", "height"}] - board is optional; the rectangle is
            in page units (1/96 in) with the origin at the top-left corner and
            defaults to a row of 240 x 160 boxes.
        schematic_path (str): Full path of the .MbsDoc; default: next to the
            project with the project's name.
        documents (list): Further files to list in the project (full paths),
            e.g. an overview schematic or an OutJob.
        sheet_size (str): "A4", "A3" or "A2" (landscape).

    Returns:
        str: JSON with the files written and the modules placed.
    """
    sizes = {"A4": (1122.52, 793.70), "A3": (1587.40, 1122.52), "A2": (2245.04, 1587.40)}
    if sheet_size not in sizes:
        return json.dumps({"success": False, "error": f"sheet_size must be one of {sorted(sizes)}"})
    project = Path(project_path)
    if project.exists():
        return json.dumps({"success": False, "error": f"project already exists: {project_path}"})
    schematic = Path(schematic_path) if schematic_path else project.with_suffix(".MbsDoc")
    if schematic.exists():
        return json.dumps({"success": False, "error": f"schematic already exists: {schematic}"})
    for m in modules:
        for key in ("designator", "title", "project"):
            if key not in m:
                return json.dumps({"success": False, "error": f"module missing '{key}': {m}"})
        if not Path(m["project"]).is_file():
            return json.dumps({"success": False, "error": f"child project not found: {m['project']}"})

    base = str(project.parent)
    next_id = [1000]

    def new_id():
        next_id[0] += 1
        return next_id[0]

    logical_modules, items = [], []
    for n, m in enumerate(modules):
        mod_id, par_id = new_id(), new_id()
        logical_modules.append({
            "designator": m["designator"], "title": m["title"],
            "source": {"sourceProject": _relative_windows_path(m["project"], str(schematic.parent)),
                       "boardFileName": (_relative_windows_path(m["board"], str(schematic.parent))
                                         if m.get("board") else "")},
            "functionalBlocks": [], "components": [], "entries": [], "entryPinMap": {"map": []},
            "parameters": [{"name": "SourceId", "value": Path(m["project"]).name, "id": par_id}],
            "id": mod_id})
        rect = {"x": float(m.get("x", 120 + n * 360)), "y": float(m.get("y", 200)),
                "width": float(m.get("width", 240)), "height": float(m.get("height", 160))}
        item_id = new_id()
        items.append({"$type": "__drawingModule", "logicalObjectId": mod_id, "rect": rect,
                      "lineStyleId": 1, "fillStyleId": 1, "anchor": 5, "selectable": True, "id": item_id})
        items.append({"$type": "__drawingLinkedProperty", "offset": {"y": 1.92}, "color": {"a": 255, "b": 128},
                      "directValue": m["designator"], "propertyValuePath": "LogicalObject.Designator",
                      "fontStyleId": 1, "autoposition": True, "isVisible": True, "anchor": 5,
                      "parentId": item_id, "selectable": True, "id": new_id()})
        items.append({"$type": "__drawingLinkedProperty", "offset": {"y": -16.1}, "color": {"a": 255, "b": 128},
                      "directName": "Title", "directValue": m["title"], "propertyValuePath": "LogicalObject.Title",
                      "fontStyleId": 1, "autoposition": True, "isVisible": True, "anchorType": 6, "group": 2,
                      "order": 1, "anchor": 5, "parentId": item_id, "selectable": True, "id": new_id()})

    styles = json.loads((MULTIBOARD_TEMPLATE / "styles.json").read_text(encoding="utf-8"))
    styles["lineStyles"] = [{"thickness": 1.889763779527559, "color": {"a": 255, "r": 112, "g": 48, "b": 160},
                             "thicknessPresetId": 2, "dashPatternPresetId": 0, "id": 1}]
    styles["fillStyles"] = [{"color": {"a": 255, "r": 146, "g": 205, "b": 220}, "hatchBackColor": {}, "id": 1}]
    width, height = sizes[sheet_size]
    page = {"size": {"width": width, "height": height},
            "margin": {"top": 10.0, "left": 10.0, "right": 10.0, "bottom": 10.0},
            "sheetSizingMode": 1, "standardSizeName": sheet_size, "horizontalZoneCount": 5,
            "verticalZoneCount": 4, "showZones": True, "items": items, "id": 1}
    logical = {"uniqueId": str(uuid.uuid4()), "modules": logical_modules, "powerPorts": [], "connections": [],
               "conflicts": [], "placeableComponents": [], "virtualModules": [], "parameters": []}
    core = {"version": 1, "modified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")}

    project.parent.mkdir(parents=True, exist_ok=True)
    schematic.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(schematic, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("styles.json", json.dumps(styles, indent=2))
        z.writestr("core.json", json.dumps(core, indent=2))
        for name in ("parameters.json", "options.json", "defaults.json"):
            z.writestr(name, (MULTIBOARD_TEMPLATE / name).read_text(encoding="utf-8"))
        z.writestr("logical.json", json.dumps(logical, indent=2))
        z.writestr("Pages/page_1.json", json.dumps(page, indent=2))

    doc_paths = [_relative_windows_path(str(schematic), base)]
    doc_paths += [_relative_windows_path(m["project"], base) for m in modules]
    doc_paths += [_relative_windows_path(d, base) for d in (documents or [])]
    project.write_text("\ufeff" + _project_ini(doc_paths), encoding="utf-8", newline="")
    return json.dumps({"success": True, "project": str(project), "schematic": str(schematic),
                       "documents": doc_paths, "modules": [m["designator"] for m in modules],
                       "next_steps": ["open_project to let Altium load it",
                                      "link_multiboard_modules to import the connectors and connect them",
                                      "run_multiboard_erc to check the result"]},
                      indent=1, ensure_ascii=False)


@mcp.tool()
async def create_project_group(ctx: Context, group_path: str, projects: list) -> str:
    """
    Write a project group (.DsnWrk): the set of projects Altium opens
    together, shown as the top node of the Projects panel.

    The file is the one File » Save Project Group As writes: an INI list of
    project paths, relative to the group file where they share its drive.

    Args:
        group_path (str): Full path of the .DsnWrk to create (must not exist).
        projects (list): Full paths of the projects (.PrjPcb, .PrjMbd,
            .PrjScr, ...) in the order the panel should list them.

    Returns:
        str: JSON with the file written and the project paths as stored.
    """
    group = Path(group_path)
    if group.exists():
        return json.dumps({"success": False, "error": f"project group already exists: {group_path}"})
    missing = [p for p in projects if not Path(p).is_file()]
    if missing or not projects:
        return json.dumps({"success": False, "error": "projects not found" if missing else "no projects given",
                           "missing": missing})
    stored = [_relative_windows_path(p, str(group.parent)) for p in projects]
    lines = ["[ProjectGroup]", "Version=1.0", ""]
    for n, path in enumerate(stored, 1):
        lines += [f"[Project{n}]", f"ProjectPath={path}", ""]
    group.parent.mkdir(parents=True, exist_ok=True)
    group.write_text("\r\n".join(lines), encoding="utf-8", newline="")
    return json.dumps({"success": True, "group": str(group), "projects": stored}, indent=1, ensure_ascii=False)


CONNECTORS_REPORT = EXCHANGE_DIR / "connectors_report.json"
ERC_REPORT = EXCHANGE_DIR / "erc_report.json"


async def _altium_report(command: str, params: dict, report: Path, what: str) -> dict:
    """Run a script command that writes a JSON report into the exchange dir and return it parsed."""
    if report.exists():
        report.unlink()
    response = await altium_bridge.execute_command(command, params)
    if not response.get("success", False):
        return {"success": False, "error": response.get("error", "unknown error")}
    if not report.is_file():
        return {"success": False, "error": f"{what} was not written", "result": response.get("result")}
    try:
        return json.loads(report.read_text(encoding=SPEC_ENCODING, errors="replace"))
    except ValueError as e:
        return {"success": False, "error": f"{what} is not valid JSON: {e}"}


@mcp.tool()
async def open_project_group(ctx: Context, group_path: str) -> str:
    """
    Open a project group (.DsnWrk) in Altium in place of the group currently
    in the workspace, as File » Open Project Group does.

    The projects of the current group are closed. Altium would ask what to do
    with unsaved work in a dialog nothing could answer, so the tool refuses to
    run while a document has unsaved changes or while a saved group has
    itself changed - and the script projects Altium adds to the group when
    this server runs commands count as such a change. It is meant for
    bringing a group into a freshly started Altium.

    Args:
        group_path (str): Full path of the .DsnWrk.

    Returns:
        str: JSON with the group now in the workspace and its projects.
    """
    data = await _altium_report("open_project_group", {"group_path": group_path}, PROJECT_REPORT, "project report")
    return json.dumps(data, indent=1, ensure_ascii=False)


def _document_unique_ids(project_file: Path) -> dict:
    """basename (lower case) -> DocumentUniqueId, from the [DocumentN] sections of a project file."""
    ids, current = {}, ""
    for line in project_file.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith("DocumentPath="):
            current = line[len("DocumentPath="):].strip().replace("/", "\\").split("\\")[-1].lower()
        elif line.startswith("DocumentUniqueId=") and current:
            ids[current] = line[len("DocumentUniqueId="):].strip()
    return ids


class _MultiboardSchematic:
    """A Multi-board Schematic (.MbsDoc): a ZIP of JSON files edited in place.

    The geometry below mirrors what Altium Designer 26 writes: a module entry
    is a 20 x 16 box docked outside the module's left edge (offset from the
    module's top-left corner) or, with dockType 2, its right edge (offset from
    the bottom-right corner); a connection point sits 8.8 units beyond the
    entry's outer face; labels are set in Times New Roman 14; ids are one
    global sequence of integers; a field whose value is zero may be absent.
    """
    ENTRY_W, ENTRY_H, ENTRY_PITCH, POINT_GAP, LABEL_GAP = 20.0, 16.0, 24.0, 8.8, 1.92
    PURPLE = {"a": 255, "r": 112, "g": 48, "b": 160}
    BLUE = {"a": 255, "r": 47, "g": 90, "b": 142}
    # Times New Roman advance widths in 1/1000 em, to place labels by their text width
    _ADVANCE = {
        **{c: 500 for c in "0123456789_"},
        **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                   (722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889,
                    722, 722, 556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611))),
        **dict(zip("abcdefghijklmnopqrstuvwxyz",
                   (444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778,
                    500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444))),
        "-": 333, " ": 250, ".": 250, "/": 278,
    }

    def __init__(self, path: Path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self.members = {i.filename: z.read(i.filename) for i in z.infolist()}
        self.logical = json.loads(self.members["logical.json"].decode("utf-8-sig"))
        self.page_name = sorted(n for n in self.members if n.lower().startswith("pages/"))[0]
        self.page = json.loads(self.members[self.page_name].decode("utf-8-sig"))
        self.styles = json.loads(self.members["styles.json"].decode("utf-8-sig"))
        self._loaded = self._snapshot()
        # A fill style holds its colour under "color"; a bare colour is put right on the way through.
        for style in self.styles.get("fillStyles", []):
            if "color" not in style and "a" in style:
                style["color"] = {k: style.pop(k) for k in ("a", "r", "g", "b") if k in style}
                style.setdefault("hatchBackColor", {})
        self.next_id = 1 + max([0] + self._ids(self.logical) + self._ids(self.page))

    def _snapshot(self) -> str:
        return json.dumps([self.logical, self.page, self.styles], sort_keys=True)

    @classmethod
    def _ids(cls, obj) -> list:
        found = []
        if isinstance(obj, dict):
            if isinstance(obj.get("id"), int):
                found.append(obj["id"])
            for v in obj.values():
                found += cls._ids(v)
        elif isinstance(obj, list):
            for v in obj:
                found += cls._ids(v)
        return found

    @classmethod
    def label_width(cls, text: str, size: float = 14.0) -> float:
        return sum(cls._ADVANCE.get(ch, 500) for ch in text) * size / 1000.0

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id - 1

    def module(self, designator: str) -> dict:
        for m in self.logical["modules"]:
            if m["designator"] == designator:
                return m
        raise KeyError(f"module {designator} not found")

    def module_item(self, module: dict) -> dict:
        for item in self.page["items"]:
            if item.get("$type") == "__drawingModule" and item.get("logicalObjectId") == module["id"]:
                return item
        raise KeyError(f"module {module['designator']} has no drawing on {self.page_name}")

    def rect(self, module: dict) -> dict:
        """The module's rectangle, with the coordinates Altium leaves out when they are zero."""
        r = self.module_item(module)["rect"]
        return {k: r.get(k, 0.0) for k in ("x", "y", "width", "height")}

    def entry_item(self, entry: dict) -> dict:
        for item in self.page["items"]:
            if item.get("$type") == "__drawingModuleEntry" and item.get("logicalObjectId") == entry["id"]:
                return item
        raise KeyError(f"entry {entry['calculatedDesignator']} has no drawing on {self.page_name}")

    def connection_group(self, connection: dict):
        for item in self.page["items"]:
            if item.get("$type") == "__drawingConnectionGroup" and item.get("logicalConnectionId") == connection["id"]:
                return item
        return None

    def _style(self, kind: str, spec: dict) -> int:
        table = self.styles.setdefault(kind, [])
        for s in table:
            if {k: v for k, v in s.items() if k != "id"} == spec:
                return s["id"]
        new = dict(spec, id=1 + max([0] + [s["id"] for s in table]))
        table.append(new)
        return new["id"]

    def entry_line_style(self) -> int:
        return self._style("lineStyles", {"thickness": 0.3779527559055119, "color": self.PURPLE,
                                          "thicknessPresetId": 0, "dashPatternPresetId": 0})

    def entry_fill_style(self) -> int:
        return self._style("fillStyles", {"hatchSpacingScale": 5.0, "hatchLineThickness": 0.3779527559055118,
                                          "color": self.PURPLE,
                                          "hatchBackColor": {"a": 255, "r": 255, "g": 255, "b": 255}})

    def point_line_style(self) -> int:
        return self._style("lineStyles", {"thickness": 0.384, "dashPattern": [], "color": self.BLUE,
                                          "thicknessPresetId": 0})

    def connection_line_style(self) -> int:
        return self._style("lineStyles", {"thickness": 1.92, "dashPattern": [2.5, 1.25], "color": self.BLUE,
                                          "thicknessPresetId": 0})

    def entry_heights(self, module: dict, side: str) -> list:
        """Heights (middle of the box) of the entries already drawn on one edge of a module."""
        item, rect, heights = self.module_item(module), self.rect(module), []
        for drawing in self.page["items"]:
            if drawing.get("$type") != "__drawingModuleEntry" or drawing.get("parentId") != item["id"]:
                continue
            y = drawing.get("offset", {}).get("y", 0.0) + self.ENTRY_H / 2
            if side == "right" and drawing.get("dockType") == 2:
                heights.append(rect["y"] + rect["height"] + y)
            elif side == "left" and drawing.get("dockType") in (None, 0):
                heights.append(rect["y"] + y)
        return heights

    def free_height(self, module: dict, wanted: float, taken: list):
        """The height nearest to wanted that is on the module's edge and ENTRY_PITCH away from
        the taken ones; None when the edge is full."""
        rect = self.rect(module)
        top, bottom = rect["y"] + self.ENTRY_H / 2, rect["y"] + rect["height"] - self.ENTRY_H / 2
        wanted = min(max(wanted, top), bottom)
        for n in range(int((bottom - top) / self.ENTRY_PITCH) + 2):
            for y in ((wanted,) if n == 0 else (wanted + n * self.ENTRY_PITCH, wanted - n * self.ENTRY_PITCH)):
                if top <= y <= bottom and all(abs(y - t) >= self.ENTRY_PITCH - 1e-6 for t in taken):
                    return y
        return None

    def _entry_placement(self, module: dict, text: str, side: str, center_y: float) -> tuple:
        """Offset and dock type of an entry on one edge of its module, offset and anchor of its label."""
        rect = self.rect(module)
        if side == "right":
            return ({"y": center_y - self.ENTRY_H / 2 - (rect["y"] + rect["height"])}, 2,
                    {"x": -(self.label_width(text) + self.LABEL_GAP), "y": -8.05}, 3)
        return ({"x": -self.ENTRY_W, "y": center_y - self.ENTRY_H / 2 - rect["y"]}, None,
                {"x": self.LABEL_GAP, "y": -8.05}, 5)

    def add_entry_drawing(self, module: dict, entry: dict, side: str, center_y: float) -> dict:
        """Dock the entry on the module's left or right edge with its middle at center_y."""
        item, text = self.module_item(module), entry["calculatedDesignator"]
        offset, dock, label_offset, label_anchor = self._entry_placement(module, text, side, center_y)
        drawing = {"$type": "__drawingModuleEntry", "offset": offset, "logicalObjectId": entry["id"],
                   "lineStyleId": self.entry_line_style(), "fillStyleId": self.entry_fill_style()}
        if dock is not None:
            drawing["dockType"] = dock
        drawing.update({"anchor": 5, "parentId": item["id"], "selectable": True, "id": self.new_id()})
        self.page["items"].append(drawing)
        self.page["items"].append({"$type": "__drawingLinkedProperty", "offset": label_offset,
                                   "color": {"a": 255, "r": 128}, "directValue": text,
                                   "propertyValuePath": "Designator", "fontStyleId": 1, "autoposition": True,
                                   "isVisible": True, "anchorType": label_anchor, "anchor": 5,
                                   "parentId": drawing["id"], "selectable": True, "id": self.new_id()})
        return drawing

    def move_entry(self, module: dict, entry: dict, side: str, center_y: float):
        """Dock an entry that is already drawn on another edge or at another height."""
        drawing = self.entry_item(entry)
        offset, dock, label_offset, label_anchor = self._entry_placement(
            module, entry["calculatedDesignator"], side, center_y)
        drawing["offset"] = offset
        drawing.pop("dockType", None)
        if dock is not None:
            drawing["dockType"] = dock
        for item in self.page["items"]:
            if item.get("$type") == "__drawingLinkedProperty" and item.get("parentId") == drawing["id"]:
                item["offset"], item["anchorType"] = label_offset, label_anchor

    def entry_point(self, module: dict, drawing: dict) -> tuple:
        """Absolute position of the connection point of an entry and its direction."""
        rect, dock = self.rect(module), drawing.get("dockType")
        if dock not in (None, 0, 2):
            raise KeyError(f"an entry of {module['designator']} is docked on an edge (dockType {dock}) "
                           "for which the connection point is not known")
        y = drawing.get("offset", {}).get("y", 0.0) + self.ENTRY_H / 2
        if dock == 2:
            return ({"x": rect["x"] + rect["width"] + self.ENTRY_W + self.POINT_GAP,
                     "y": rect["y"] + rect["height"] + y}, {"x": 1.0})
        return {"x": rect["x"] - self.ENTRY_W - self.POINT_GAP, "y": rect["y"] + y}, {"x": -1.0}

    def add_connection_drawing(self, connection: dict, ends: list) -> dict:
        """Draw a connection between two entries; ends: [(module, entry), (module, entry)].

        The group holds the two end points (each labelled with the entry at
        the other end), the connection designator and the connection item,
        whose children - corner points and the segments chained through
        them - are the line that is actually drawn.
        """
        point_style, line_style = self.point_line_style(), self.connection_line_style()
        group = {"$type": "__drawingConnectionGroup", "moduleEntryIds": [entry["id"] for _, entry in ends],
                 "lineStyleId": line_style, "designatorPointId": 0, "logicalConnectionId": connection["id"],
                 "anchor": 5, "selectable": True, "id": self.new_id()}
        points, labels = [], []
        for n, (module, entry) in enumerate(ends):
            other_module, other_entry = ends[1 - n]
            drawing = self.entry_item(entry)
            position, direction = self.entry_point(module, drawing)
            point = {"$type": "__drawingConnectionPoint", "position": position, "direction": direction,
                     "pinId": drawing["id"], "lineStyleId": point_style, "anchor": 5,
                     "parentId": group["id"], "selectable": True, "id": self.new_id()}
            text = f"{other_module['designator']}-{other_entry['calculatedDesignator']}"
            offset = self.LABEL_GAP if direction["x"] > 0 else -(self.label_width(text) + self.LABEL_GAP)
            labels.append({"$type": "__drawingLinkedProperty", "offset": {"x": offset},
                           "color": {"a": 255, "r": 128}, "directValue": text, "propertyValuePath": "Designator",
                           "fontStyleId": 1, "autoposition": True, "isVisible": True, "anchor": 5,
                           "parentId": point["id"], "selectable": True, "id": self.new_id()})
            points.append(point)
        group["designatorPointId"] = points[0]["id"]
        line = {"$type": "__drawingConnection", "endItemId1": points[0]["id"], "endItemId2": points[1]["id"],
                "lineStyleId": line_style, "anchor": 5, "parentId": group["id"], "selectable": True,
                "id": self.new_id()}
        # route: out of the first entry, across at the middle, into the second entry
        p1, p2 = points[0]["position"], points[1]["position"]
        middle = (p1["x"] + p2["x"]) / 2
        corners = [{"x": middle, "y": p1["y"]}]
        if abs(p1["y"] - p2["y"]) > 0.01:
            corners.append({"x": middle, "y": p2["y"]})
        route, chain = [], [points[0]["id"]]
        for corner in corners:
            item = {"$type": "__drawingConnectionPoint", "position": corner, "direction": {},
                    "connectionItemType": 1, "lineStyleId": point_style, "anchor": 5, "parentId": line["id"],
                    "selectable": False, "id": self.new_id()}
            route.append(item)
            chain.append(item["id"])
        chain.append(points[1]["id"])
        for start, end in zip(chain, chain[1:]):
            route.append({"$type": "__drawingConnection", "endItemId1": start, "endItemId2": end,
                          "lineStyleId": line_style, "connectionItemType": 1, "anchor": 5,
                          "parentId": line["id"], "selectable": True, "id": self.new_id()})
        designator = {"$type": "__drawingLinkedProperty", "offset": {"x": (middle - p1["x"]) / 2 - 8.0, "y": -18.0},
                      "color": {"a": 255, "b": 128}, "directValue": connection["designator"],
                      "propertyValuePath": "Designator", "fontStyleId": 1, "isVisible": True, "anchor": 5,
                      "parentId": group["id"], "selectable": True, "id": self.new_id()}
        self.page["items"] += [group] + points + labels + [designator, line] + route
        return group

    def rename_connector(self, module: dict, component: dict, designator: str):
        """Give a connector, its entry and the entry's label a new designator."""
        component["designator"] = designator
        pin_ids = {p["id"] for p in component["pins"]}
        entry_ids = {m["value"] for m in module.get("entryPinMap", {}).get("map", []) if m["key"] in pin_ids}
        for entry in module.get("entries", []):
            if entry["id"] in entry_ids:
                entry["calculatedDesignator"] = designator
        drawn = {i["id"] for i in self.page["items"]
                 if i.get("$type") == "__drawingModuleEntry" and i.get("logicalObjectId") in entry_ids}
        for item in self.page["items"]:
            if item.get("$type") == "__drawingLinkedProperty" and item.get("parentId") in drawn:
                item["directValue"] = designator

    def relabel_connection_ends(self):
        """Rewrite the label at each end of every drawn connection: module and connector at the other end."""
        names = {}
        for module in self.logical["modules"]:
            entries = {e["id"]: e["calculatedDesignator"] for e in module.get("entries", [])}
            for item in self.page["items"]:
                if item.get("$type") == "__drawingModuleEntry" and item.get("logicalObjectId") in entries:
                    names[item["id"]] = f"{module['designator']}-{entries[item['logicalObjectId']]}"
        for group in [i for i in self.page["items"] if i.get("$type") == "__drawingConnectionGroup"]:
            ends = [i for i in self.page["items"] if i.get("$type") == "__drawingConnectionPoint"
                    and i.get("parentId") == group["id"] and i.get("pinId") in names]
            if len(ends) != 2:
                continue
            for point, other in (ends, ends[::-1]):
                for label in self.page["items"]:
                    if label.get("$type") == "__drawingLinkedProperty" and label.get("parentId") == point["id"]:
                        label["directValue"] = names[other["pinId"]]

    def save(self) -> bool:
        """Rewrite the document; returns False, touching nothing, when it has not changed."""
        if self._snapshot() == self._loaded:
            return False
        core = {"version": 1, "modified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")}
        self.members["logical.json"] = json.dumps(self.logical, indent=2).encode("utf-8")
        self.members[self.page_name] = json.dumps(self.page, indent=2).encode("utf-8")
        self.members["styles.json"] = json.dumps(self.styles, indent=2).encode("utf-8")
        self.members["core.json"] = json.dumps(core, indent=2).encode("utf-8")
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in self.members.items():
                z.writestr(name, data)
        self._loaded = self._snapshot()
        return True


def _refresh_connector_nets(component: dict, connector: dict, new_id) -> tuple:
    """Bring the pin nets of an imported connector in line with the compiled project.

    Returns (pins whose net changed, pins present on one side only).
    """
    fresh = {p["number"]: p for p in connector["pins"]}
    changed, differing = [], []
    for pin in component["pins"]:
        source = fresh.pop(pin["number"], None)
        if source is None:
            differing.append(pin["number"])
            continue
        params = pin["parameters"]
        current = next((p for p in params if p["name"] == "ExternalNetName"), None)
        if source["net"] and current is None:
            params.append({"name": "ExternalNetName", "value": source["net"], "id": new_id()})
        elif source["net"] and current["value"] != source["net"]:
            current["value"] = source["net"]
        elif not source["net"] and current is not None:
            params.remove(current)
        else:
            continue
        changed.append(pin["number"])
    return changed, differing + list(fresh)


def _pin_sort_key(pin: dict):
    number = pin["number"]
    return (0, int(number)) if number.isdigit() else (1, number)


@mcp.tool()
async def link_multiboard_modules(ctx: Context, schematic_path: str, connections: list = None) -> str:
    """
    Bring the connectors of the child projects into a Multi-board Schematic
    and connect them, as Design » Import From Child Projects and Place »
    Direct Connection do in the GUI.

    Every module whose source is a .PrjPcb is compiled in Altium; the child
    projects are opened in the workspace and only read. Each of their
    components with a parameter System = Connector becomes a module entry:
    component, pins with their compiled nets, entry, pin map and the entry
    box drawn on the module edge that faces its partner. A connection pairs
    the pins of two entries by pin designator and draws the line between
    them; an entry takes one connection.

    On a later run a connector that is already in the schematic keeps its
    entry and gets its pin nets, designator and comment brought up to date.
    Connectors that left the child project and pins that exist on one side
    only are reported, not rebuilt. The document is rewritten on disk only
    when something changed; run run_multiboard_erc (which reloads it) next.

    Args:
        schematic_path (str): Full path of the .MbsDoc.
        connections (list): [{"from": "M1:J1", "to": "M2:J1"}] - module
            designator and connector designator of each end.

    Returns:
        str: JSON with the connectors imported, renamed or refreshed per
             module, the connections made (pin pairs, unpaired pins, pins
             whose nets differ), warnings and whether the document was
             written.
    """
    path = Path(schematic_path)
    if not path.is_file():
        return json.dumps({"success": False, "error": f"schematic not found: {schematic_path}"})
    try:
        mbs = _MultiboardSchematic(path)
    except (KeyError, IndexError, ValueError, OSError, zipfile.BadZipFile) as e:
        return json.dumps({"success": False, "error": f"cannot read the schematic: {e}"})
    wanted = []
    for c in connections or []:
        try:
            a, b = c["from"].split(":", 1), c["to"].split(":", 1)
            wanted.append(((a[0].strip(), a[1].strip()), (b[0].strip(), b[1].strip())))
        except (KeyError, IndexError, AttributeError, TypeError):
            return json.dumps({"success": False, "error": f"connection must be {{'from': 'M1:J1', 'to': 'M2:J1'}}: {c}"})
    rects = {}
    for m in mbs.logical["modules"]:
        try:
            rects[m["designator"]] = mbs.rect(m)
        except KeyError:
            pass

    def partner_rect(module: str, connector: str):
        for a, b in wanted:
            if a == (module, connector):
                return rects.get(b[0])
            if b == (module, connector):
                return rects.get(a[0])
        return None

    def facing(own: dict, far) -> tuple:
        """The edge of a module that faces another one, and the middle of the vertical span
        the two share, where a level line can leave it; left edge, mid-height without a partner."""
        middle = own["y"] + own["height"] / 2
        if not far:
            return "left", middle
        side = "right" if far["x"] + far["width"] / 2 > own["x"] + own["width"] / 2 else "left"
        top, bottom = max(own["y"], far["y"]), min(own["y"] + own["height"], far["y"] + far["height"])
        return side, (top + bottom) / 2 if bottom - top >= mbs.ENTRY_H else middle

    warnings, report_modules = [], []
    with_source, any_renamed = 0, False
    for module in mbs.logical["modules"]:
        source = (module.get("source") or {}).get("sourceProject") or ""
        if not source.lower().endswith(".prjpcb"):
            continue
        with_source += 1
        project = Path(os.path.normpath(os.path.join(str(path.parent), source)))
        if not project.is_file():
            warnings.append(f"{module['designator']}: child project not found: {project}")
            continue
        data = await _altium_report("get_project_connectors", {"project_path": str(project)},
                                    CONNECTORS_REPORT, "connectors report")
        if not data.get("success"):
            return json.dumps({"success": False, "module": module["designator"], **data}, ensure_ascii=False)
        doc_ids = _document_unique_ids(project)
        present = {p["value"]: c for c in module.get("components", []) for p in c.get("parameters", [])
                   if p.get("name") == "SourceId"}
        added, kept, renamed, refreshed, seen = [], [], {}, {}, set()
        new_entries = []
        for conn in data.get("connectors", []):
            if conn["unique_id"] in present:
                component = present[conn["unique_id"]]
                seen.add(conn["unique_id"])
                if component["designator"] != conn["designator"]:
                    renamed[component["designator"]] = conn["designator"]
                    mbs.rename_connector(module, component, conn["designator"])
                component["comment"] = conn["comment"]
                for p in component["parameters"]:
                    if p["name"] == "PhysicalPath":
                        p["value"] = conn["physical_path"]
                kept.append(conn["designator"])
                changed, differing = _refresh_connector_nets(component, conn, mbs.new_id)
                if changed:
                    refreshed[conn["designator"]] = changed
                if differing:
                    warnings.append(f"{module['designator']}.{conn['designator']}: pins {', '.join(differing)} exist "
                                    "on one side only (schematic or project); the entry was not rebuilt")
                continue
            doc_name = conn["document"].replace("/", "\\").split("\\")[-1].lower()
            parent_id = doc_ids.get(doc_name, "")
            if not parent_id:
                warnings.append(f"{module['designator']}.{conn['designator']}: no DocumentUniqueId for {doc_name} in {project.name}")
            pins = []
            for pin in sorted(conn["pins"], key=_pin_sort_key):
                params = [{"name": "SourceId", "value": pin["unique_id"], "id": mbs.new_id()},
                          {"name": "ParentSourceId", "value": conn["unique_id"], "id": mbs.new_id()}]
                if pin["net"]:
                    params.append({"name": "ExternalNetName", "value": pin["net"], "id": mbs.new_id()})
                if not pin["unique_id"]:
                    warnings.append(f"{module['designator']}.{conn['designator']} pin {pin['number']}: no unique id")
                pins.append({"pinId": pin["number"], "name": pin["name"], "number": pin["number"],
                             "parameters": params, "id": mbs.new_id()})
            component = {"designator": conn["designator"], "comment": conn["comment"], "pins": pins,
                         "parameters": [{"name": "ParentSourceId", "value": parent_id, "id": mbs.new_id()},
                                        {"name": "PhysicalPath", "value": conn["physical_path"], "id": mbs.new_id()},
                                        {"name": "SourceId", "value": conn["unique_id"], "id": mbs.new_id()}],
                         "id": mbs.new_id()}
            entry = {"calculatedDesignator": conn["designator"], "parameters": [], "id": mbs.new_id()}
            module.setdefault("components", []).append(component)
            module.setdefault("entries", []).append(entry)
            pin_map = module.setdefault("entryPinMap", {"map": []}).setdefault("map", [])
            pin_map += [{"key": p["id"], "value": entry["id"]} for p in pins]
            new_entries.append(entry)
            added.append(conn["designator"])
        for unique_id, component in present.items():
            if unique_id not in seen:
                warnings.append(f"{module['designator']}.{component['designator']}: no longer a connector in the "
                                "child project; its entry was left in place")
        any_renamed = any_renamed or bool(renamed)
        # draw the new entries on the edge facing their partner, clear of the entries already there
        if module["designator"] not in rects:
            if new_entries:
                warnings.append(f"{module['designator']}: no drawing on the page, entries not drawn")
        else:
            taken = {side: mbs.entry_heights(module, side) for side in ("left", "right")}
            placed = [facing(rects[module["designator"]],
                             partner_rect(module["designator"], e["calculatedDesignator"])) + (e,)
                      for e in new_entries]
            for side, height, entry in sorted(placed, key=lambda t: t[:2]):
                y = mbs.free_height(module, height, taken[side])
                if y is None:
                    y = height
                    warnings.append(f"{module['designator']}: the {side} edge has no room for "
                                    f"{entry['calculatedDesignator']}; it overlaps another entry")
                taken[side].append(y)
                mbs.add_entry_drawing(module, entry, side, y)
        report_modules.append({"designator": module["designator"], "project": str(project),
                               "connectors_added": added, "connectors_present": kept,
                               "connectors_renamed": renamed, "nets_updated": refreshed})
    if with_source and not report_modules:
        return json.dumps({"success": False, "error": "none of the child projects of the schematic was found",
                           "warnings": warnings}, indent=1, ensure_ascii=False)
    if any_renamed:
        mbs.relabel_connection_ends()

    def find_entry(module_designator: str, connector: str):
        module = mbs.module(module_designator)
        for entry in module.get("entries", []):
            if entry["calculatedDesignator"] == connector:
                for component in module.get("components", []):
                    if component["designator"] == connector:
                        return module, entry, component
        raise KeyError(f"{module_designator} has no connector {connector}")

    report_connections = []
    # Other kinds of connection (harness, cable) are stored differently and are left alone.
    direct = {tuple(sorted((c["entriesConnection"]["entry1"], c["entriesConnection"]["entry2"]))): c
              for c in mbs.logical["connections"] if "entriesConnection" in c}
    for (ma, ca), (mb, cb) in wanted:
        name = f"{ma}:{ca} - {mb}:{cb}"
        try:
            module_a, entry_a, comp_a = find_entry(ma, ca)
            module_b, entry_b, comp_b = find_entry(mb, cb)
        except KeyError as e:
            warnings.append(e.args[0])
            continue
        if entry_a is entry_b:
            warnings.append(f"{name}: a connector cannot be connected to itself")
            continue
        ends = [(module_a, entry_a), (module_b, entry_b)]
        try:  # both ends must be drawable before anything is recorded
            for end_module, end_entry in ends:
                mbs.entry_point(end_module, mbs.entry_item(end_entry))
        except KeyError as e:
            warnings.append(f"{name} not connected: {e.args[0]}")
            continue
        known = direct.get(tuple(sorted((entry_a["id"], entry_b["id"]))))
        if known is not None:
            if mbs.connection_group(known) is None:
                mbs.add_connection_drawing(known, ends)
                warnings.append(f"{name} already connected ({known.get('designator')}); its drawing was restored")
            else:
                warnings.append(f"{name} already connected ({known.get('designator')})")
            continue
        busy = [end for end, entry in ((f"{ma}:{ca}", entry_a), (f"{mb}:{cb}", entry_b))
                if entry.get("logicalConnection")]
        if busy:
            warnings.append(f"{name} not connected: {', '.join(busy)} already has a connection")
            continue
        # an entry without a connection is free to move to the edge that faces its partner
        for (end_module, end_entry), (far_module, _) in (ends, ends[::-1]):
            side, height = facing(rects[end_module["designator"]], rects[far_module["designator"]])
            if (mbs.entry_item(end_entry).get("dockType") == 2) != (side == "right"):
                y = mbs.free_height(end_module, height, mbs.entry_heights(end_module, side))
                if y is not None:
                    mbs.move_entry(end_module, end_entry, side, y)
        pins_b = {p["number"]: p for p in comp_b["pins"]}
        pairs, unpaired, conflicts = [], [], []
        for pin_a in sorted(comp_a["pins"], key=_pin_sort_key):
            pin_b = pins_b.pop(pin_a["number"], None)
            if pin_b is None:
                unpaired.append(f"{ma}:{ca}-{pin_a['number']}")
                continue
            pairs.append({"$type": "__logicalPinToPinConnection",
                          "pinPair": {"pin1": pin_a["id"], "pin2": pin_b["id"], "parameters": [], "id": mbs.new_id()},
                          "designator": pin_a["number"], "parameters": [], "id": mbs.new_id()})
            net_a = next((p["value"] for p in pin_a["parameters"] if p["name"] == "ExternalNetName"), "")
            net_b = next((p["value"] for p in pin_b["parameters"] if p["name"] == "ExternalNetName"), "")
            if net_a and net_b and net_a != net_b:
                conflicts.append({"pin": pin_a["number"], ma: net_a, mb: net_b})
        unpaired += [f"{mb}:{cb}-{n}" for n in pins_b]
        used = {c.get("designator") for c in mbs.logical["connections"]}
        designator = next(f"C{n}" for n in range(1, len(used) + 2) if f"C{n}" not in used)
        connection = {"$type": "__logicalDirectConnection",
                      "entriesConnection": {"entry1": entry_a["id"], "entry2": entry_b["id"],
                                            "physicalConnections": pairs, "id": mbs.new_id()},
                      "designator": designator, "parameters": [], "id": mbs.new_id()}
        entry_a["logicalConnection"] = connection["id"]
        entry_b["logicalConnection"] = connection["id"]
        mbs.logical["connections"].append(connection)
        direct[tuple(sorted((entry_a["id"], entry_b["id"])))] = connection
        mbs.add_connection_drawing(connection, ends)
        report_connections.append({"designator": designator, "from": f"{ma}:{ca}", "to": f"{mb}:{cb}",
                                   "pin_pairs": len(pairs), "unpaired": unpaired, "net_conflicts": conflicts})
    written = mbs.save()
    return json.dumps({"success": True, "schematic": str(path), "written": written, "modules": report_modules,
                       "connections": report_connections, "warnings": warnings,
                       "next_steps": ["run_multiboard_erc reloads the schematic in Altium and checks it"]},
                      indent=1, ensure_ascii=False)


@mcp.tool()
async def run_multiboard_erc(ctx: Context, schematic_path: str) -> str:
    """
    Reload a Multi-board Schematic (.MbsDoc) from disk, run its ERC (Design »
    Run ERC) and return the Messages panel.

    A schematic with unsaved changes is refused, because the reload would
    discard them. The Messages panel is cleared before the run.

    Args:
        schematic_path (str): Full path of the .MbsDoc.

    Returns:
        str: JSON with errors (messages of class Error or Fatal Error),
             warnings and messages[] (class, text, source, document).
    """
    data = await _altium_report("run_multiboard_erc", {"document_path": schematic_path}, ERC_REPORT, "ERC report")
    return json.dumps(data, indent=1, ensure_ascii=False)


@mcp.tool()
async def save_documents(ctx: Context, paths: list) -> str:
    """
    Save open Altium documents by path (.SchDoc, .SchLib, .PcbLib, .PcbDoc).

    The library creation tools work on the open library in memory; call this
    to write the result to disk when the user does not save it in Altium.

    Args:
        paths (list): Full paths of documents that are open in Altium.

    Returns:
        str: JSON with saved[] and not_saved[] (not open, or save refused).
    """
    logger.info(f"save_documents {paths}")
    response = await altium_bridge.execute_command("save_documents", {"paths": paths})
    if not response.get("success", False):
        return json.dumps({"success": False, "error": response.get("error", "unknown error")})
    result = response.get("result", {})
    return json.dumps(result, indent=2, ensure_ascii=False) if not isinstance(result, str) else result


@mcp.tool()
async def create_symbols_batch(ctx: Context, spec_file: str) -> str:
    """
    Create many schematic symbols in a single Altium script run.

    Use instead of repeated create_schematic_symbol calls when creating
    more than a handful of symbols (bulk library imports/migrations): one
    script launch instead of one per symbol, and pipe-delimited plain text
    instead of JSON so field text (commas, brackets, spaces) is preserved
    exactly. Verified by exact round-trips of a complete 284-symbol
    production library.

    Args:
        spec_file (str): Path to a plain-text spec file, one record per line:
            LIBRARY|<path to .SchLib>   (optional first line: opens/focuses;
                                         an already-open library is only
                                         focused, never reloaded)
            SYMBOL|<name>|<description>|<part_count>
            PIN|<same pipe fields as create_schematic_symbol pins>
            GRAPHIC|<same entry format as create_schematic_symbol graphics>
            Each SYMBOL line starts a new symbol; PIN/GRAPHIC lines belong
            to the most recent SYMBOL.

    Returns:
        str: JSON object with created count and a failed name list
    """
    logger.info(f"Creating symbols batch from {spec_file}")

    response = await altium_bridge.execute_command(
        "create_symbols_batch",
        {"spec_file": spec_file}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error in batch symbol creation: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed batch creation: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def get_symbol_primitives(ctx: Context, library_path: str = "", symbol_name: str = "") -> str:
    """
    Read the graphic primitives of symbols in a schematic library (.SchLib).

    Two modes:
    - symbol_name omitted: inventory of every symbol in the library with
      per-type primitive counts (pins, rectangles, lines, polylines,
      polygons, arcs, ellipses, beziers, labels, ...). Use this to survey
      what drawing features a library's symbols require.
    - symbol_name given (exact match, case-insensitive): full geometry dump
      of that symbol - every primitive with coordinates in mils, plus pin
      details (number, name, electrical type, orientation, length,
      owner_part_id). Use this as the reference/spec when recreating or
      validating a symbol.

    Args:
        library_path (str, optional): Full path to the .SchLib file to open.
            Omit to use the schematic library currently focused in Altium.
        symbol_name (str, optional): Exact symbol (LibReference) name to dump.

    Returns:
        str: JSON object - inventory mode: {library_name, symbol_count,
             symbols: [{name, description, part_count, <type counts>}]};
             dump mode: {library_name, symbol_name, description, part_count,
             primitives: [...]}
    """
    logger.info(f"Getting symbol primitives (library={library_path}, symbol={symbol_name})")

    response = await altium_bridge.execute_command(
        "get_symbol_primitives",
        {"library_path": library_path, "symbol_name": symbol_name}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting symbol primitives: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to get symbol primitives: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def get_all_nets(ctx: Context) -> str:
    """
    Return every unique net name in the active PCB document.

    Returns
    -------
    str :
        A JSON array of net names, e.g. ["GND", "VCC33", "USB_D+", ...]
    """
    logger.info("Getting all nets")

    response = await altium_bridge.execute_command("get_all_nets", {})

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting nets: {error_msg}")
        return json.dumps({"error": f"Failed to get nets: {error_msg}"})

    # Result is already a JSON‑serialisable Python list
    return json.dumps(response.get("result", []), indent=2)

@mcp.tool()
async def create_net_class(ctx: Context, class_name: str, net_names: list) -> str:
    """
    Create a new net class and add specified nets to it
    
    Args:
        class_name (str): Name of the net class to create or modify
        net_names (list): List of net names to add to the class
    
    Returns:
        str: JSON object with the result of the operation
    """
    logger.info(f"Creating net class '{class_name}' with {len(net_names)} nets")
    
    # Execute the command in Altium to create the net class
    response = await altium_bridge.execute_command(
        "create_net_class",
        {
            "class_name": class_name,
            "net_names": net_names
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating net class: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create net class: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Net class '{class_name}' created/modified successfully")
    return json.dumps(result, indent=2)
    
@mcp.tool()
async def set_component_position(ctx: Context, cmp_designator: str, x: float, y: float, rotation: float = -1) -> str:
    """
    Set a component's absolute position in the PCB layout
    
    Args:
        cmp_designator (str): Designator of the component to position (e.g., "R1", "C5", "U3")
        x (float): Absolute X position in mils
        y (float): Absolute Y position in mils
        rotation (float): Rotation angle in degrees (0-360), use -1 to keep current rotation
    
    Returns:
        str: JSON object with the result of the position operation
    """
    logger.info(f"Setting component {cmp_designator} position to X:{x}, Y:{y}, Rotation:{rotation}")
    
    response = await altium_bridge.execute_command(
        "set_component_position",
        {
            "designator": cmp_designator,
            "x": x,
            "y": y,
            "rotation": rotation
        }
    )
    
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error setting component position: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to set component position: {error_msg}"})
    
    result = response.get("result", {})
    logger.info(f"Component position set successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

@mcp.tool()
async def move_components(ctx: Context, cmp_designators: list, x_offset: float, y_offset: float, rotation: float = 0) -> str:
    """
    Move components by RELATIVE offset from their current position (not absolute positioning)
    
    IMPORTANT: This moves components BY the offset amount, not TO a position.
    For absolute positioning, use set_component_position instead.
    
    Args:
        cmp_designators (list): List of designators of the components to move (e.g., ["R1", "C5", "U3"])
        x_offset (float): X offset distance in mils (positive = right, negative = left)
        y_offset (float): Y offset distance in mils (positive = up, negative = down)
        rotation (float): New absolute rotation angle in degrees (0-360), if 0 the rotation is not changed
    
    Returns:
        str: JSON object with the result of the move operation
    """
    logger.info(f"Moving components: {cmp_designators} by X:{x_offset}, Y:{y_offset}, Rotation:{rotation}")
    
    # Execute the command in Altium to move components
    response = await altium_bridge.execute_command(
        "move_components",
        {
            "designators": cmp_designators,
            "x_offset": x_offset,
            "y_offset": y_offset,
            "rotation": rotation
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error moving components: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to move components: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})

    logger.info(f"Components moved successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

@mcp.tool()
async def place_components(ctx: Context, placements: list) -> str:
    """
    Place multiple components at absolute positions in a single Altium transaction.

    This is the batch version of set_component_position - prefer it whenever
    placing more than one component, since every tool call is a full round
    trip into Altium. The whole batch is one undo step.

    Coordinate conventions: x/y are in mils relative to the board origin;
    rotation is in degrees counterclockwise.

    Args:
        placements (list): One dict per component:
            - designator (str, required): e.g. "R1"
            - x (float, required): absolute X position in mils
            - y (float, required): absolute Y position in mils
            - rotation (float, optional): absolute rotation in degrees (0-360).
              Omit (or pass -1) to keep the current rotation.
            - layer (str, optional): "top" or "bottom" to set the board side
              (the footprint is mirrored when flipped). Omit to keep the
              current side.

    Example:
        placements=[{"designator": "R1", "x": 1000, "y": 2000, "rotation": 90},
                    {"designator": "C5", "x": 1050, "y": 2000, "layer": "bottom"}]

    Returns:
        str: JSON object with placed_count, missing_designators, and the final
             x/y/rotation/layer of each placed component as Altium reports them
    """
    logger.info(f"Placing {len(placements)} components")

    # Flatten each placement into a pipe-delimited string
    # ('Designator|X|Y|Rotation|Layer') - the DelphiScript side parses the
    # request line by line, so nested JSON objects are not safe to send
    entries = []
    errors = []
    for idx, placement in enumerate(placements):
        if not isinstance(placement, dict):
            errors.append(f"placements[{idx}] must be an object")
            continue

        designator = str(placement.get("designator", "")).strip()
        x = placement.get("x")
        y = placement.get("y")

        if not designator or x is None or y is None:
            errors.append(f"placements[{idx}] must include designator, x, and y")
            continue
        if "|" in designator:
            errors.append(f"placements[{idx}] designator must not contain '|'")
            continue

        rotation = placement.get("rotation", -1)
        layer = str(placement.get("layer", "") or "").strip().lower()
        if layer not in ("", "top", "bottom"):
            errors.append(f"placements[{idx}] layer must be 'top' or 'bottom'")
            continue

        try:
            entry = f"{designator}|{float(x)}|{float(y)}|{float(rotation)}|{layer}"
        except (TypeError, ValueError):
            errors.append(f"placements[{idx}] x, y, and rotation must be numbers")
            continue
        entries.append(entry)

    if errors:
        return json.dumps({"success": False, "error": "; ".join(errors)})
    if not entries:
        return json.dumps({"success": False, "error": "No placements provided"})

    response = await altium_bridge.execute_command(
        "place_components",
        {"placements": entries}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error placing components: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to place components: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Placed components successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

def _mst_length(points: list) -> float:
    """Minimum-spanning-tree length over (x, y) points (Prim's algorithm)."""
    n = len(points)
    if n < 2:
        return 0.0
    import math
    in_tree = [False] * n
    best = [float("inf")] * n
    best[0] = 0.0
    total = 0.0
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: best[i])
        in_tree[u] = True
        total += best[u]
        ux, uy = points[u]
        for v in range(n):
            if not in_tree[v]:
                d = math.dist((ux, uy), points[v])
                if d < best[v]:
                    best[v] = d
    return total

@mcp.tool()
async def get_net_connections(ctx: Context, cmp_designators: list = None, max_pads_per_net: int = 40) -> str:
    """
    Get net connectivity and airline (unrouted connection) lengths for the
    nets touching the given components.

    Use this to plan or score a placement: it shows every pad on each net -
    including pads of OTHER components outside the given set (e.g. an input
    filter the cluster must connect to) - plus the net's minimum-spanning-tree
    airline length in mils. Shorter airlines on critical nets (switching
    loops, input/output capacitors) mean a better placement; non-critical
    nets (enables, set resistors, feedback dividers) may be lengthened to
    buy routing space. Plane nets like GND have many pads and a meaningless
    airline - judge them by proximity to plane connections instead.

    Args:
        cmp_designators (list, optional): Components whose nets to analyze
            (e.g. ["U12", "R42"]). Omit to use the current Altium selection.
        max_pads_per_net (int): Nets with more pads than this (e.g. GND)
            return only pads belonging to the given components, plus the
            total pad_count. Their airline is also skipped. Default 40.

    Returns:
        str: JSON object with one entry per net: pad_count,
             airline_mst_mils (None for large nets), and pads
             [{designator, pin, x, y}, ...] in mils relative to the board
             origin. Large nets set pads_truncated=true.
    """
    logger.info(f"Getting net connections (designators={cmp_designators})")

    params = {}
    if cmp_designators:
        params["designators"] = cmp_designators

    response = await altium_bridge.execute_command("get_net_connections", params)

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting net connections: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to get net connections: {error_msg}"})

    result = response.get("result", {})
    if isinstance(result, str):
        result = json.loads(result)

    target_set = set(cmp_designators or result.get("targets", []))

    # Group the flat pad list by net and compute airline lengths
    nets = {}
    for pad in result.get("pads", []):
        nets.setdefault(pad["net"], []).append(pad)

    out_nets = []
    for name in result.get("net_names", []):
        pads = nets.get(name, [])
        entry = {"net": name, "pad_count": len(pads)}
        if len(pads) <= max_pads_per_net:
            entry["airline_mst_mils"] = round(_mst_length([(p["x"], p["y"]) for p in pads]), 1)
            entry["pads"] = [
                {"designator": p["designator"], "pin": p["pin"], "x": p["x"], "y": p["y"]}
                for p in pads
            ]
        else:
            entry["airline_mst_mils"] = None
            entry["pads_truncated"] = True
            entry["pads"] = [
                {"designator": p["designator"], "pin": p["pin"], "x": p["x"], "y": p["y"]}
                for p in pads
                if not target_set or p["designator"] in target_set
            ]
        out_nets.append(entry)

    out_nets.sort(key=lambda e: (e["airline_mst_mils"] is None, -(e["airline_mst_mils"] or 0)))

    logger.info(f"Net connections: {len(out_nets)} nets")
    return json.dumps({"net_count": len(out_nets), "nets": out_nets}, indent=2)

def _rotate_offset(dx: float, dy: float, degrees: float) -> tuple:
    """Rotate a rotation-0 pad offset counterclockwise by the given angle."""
    import math
    rad = math.radians(degrees)
    return (dx * math.cos(rad) - dy * math.sin(rad),
            dx * math.sin(rad) + dy * math.cos(rad))

@mcp.tool()
async def check_orientation(ctx: Context, cmp_designators: list = None, min_improvement_mils: float = 25) -> str:
    """
    Advisory check: find 2-pad passives whose rotation could be improved.

    For each 2-pad component in the target set, every orthogonal rotation
    (0/90/180/270) is scored in place, summing one term per pad:
    - routable nets (few pads): the net's total airline (MST) length with
      this pad at the candidate position - so a pad that merely slides along
      a pass-through flow (e.g. a bulk cap in a power chain) scores as
      nearly free, while a genuine detour costs its real length
    - plane nets (many pads, e.g. GND): distance to the nearest same-net
      pad - a capacitor's ground-return loop is local, so proximity to the
      IC's GND/thermal pad is what matters
    A component is reported when a different rotation beats the current one
    by at least min_improvement_mils.

    This is advisory, not pass/fail: it measures geometry only and knows
    nothing about net criticality. Suggestions matter for loop-critical
    parts (capacitor ground returns, snubbers, input/output filters) and
    should usually be ignored for parts whose orientation is electrically
    arbitrary (pull-ups, strapping resistors, enables) or where a datasheet
    layout recommendation says otherwise. Weigh suggestions with judgement
    rather than applying them blindly. If a suggestion with
    bbox_changes=true is applied (a 90-degree change alters the body
    outline), re-run check_placement afterwards.

    Args:
        cmp_designators (list, optional): Components to check. Omit to use
            the current Altium selection. Components with more or fewer than
            2 pads are skipped.
        min_improvement_mils (float): Only report components where the best
            rotation improves the connection score by at least this many
            mils (default 25).

    Returns:
        str: JSON object with checked_count and suggestions, each having
             designator, current_rotation, suggested_rotation,
             improvement_mils, bbox_changes, and a per-pad breakdown of
             nearest same-net distances at the current vs suggested rotation.
    """
    import math

    logger.info(f"Checking orientation (designators={cmp_designators})")

    # Resolve the target designators from the selection when not given
    if not cmp_designators:
        sel_resp = await altium_bridge.execute_command("get_selected_components_coordinates", {})
        if not sel_resp.get("success", False):
            return json.dumps({"success": False, "error": sel_resp.get("error", "Unknown error")})
        sel = sel_resp.get("result", [])
        if isinstance(sel, str):
            sel = json.loads(sel)
        cmp_designators = [c["designator"] for c in sel if "designator" in c]
        if not cmp_designators:
            return json.dumps({"success": False, "error": "No components selected and no designators given"})

    pins_resp = await altium_bridge.execute_command("get_component_pins", {"designators": cmp_designators})
    if not pins_resp.get("success", False):
        return json.dumps({"success": False, "error": pins_resp.get("error", "Unknown error")})
    comps = pins_resp.get("result", [])
    if isinstance(comps, str):
        comps = json.loads(comps)

    nets_resp = await altium_bridge.execute_command("get_net_connections", {"designators": cmp_designators})
    if not nets_resp.get("success", False):
        return json.dumps({"success": False, "error": nets_resp.get("error", "Unknown error")})
    net_data = nets_resp.get("result", {})
    if isinstance(net_data, str):
        net_data = json.loads(net_data)

    # net name -> list of (designator, x, y) for every pad on the net
    net_pads = {}
    for p in net_data.get("pads", []):
        net_pads.setdefault(p["net"], []).append((p["designator"], p["x"], p["y"]))

    PLANE_NET_PAD_COUNT = 40  # nets above this are treated as planes

    suggestions = []
    checked = 0
    for comp in comps:
        pins = comp.get("pins", [])
        if len(pins) != 2 or "x" not in comp:
            continue
        mirror = comp.get("layer") == "Bottom Layer"

        def score(rotation):
            """Hybrid per-pad score (see docstring); None if no pad scores."""
            total, detail = 0.0, []
            for pin in pins:
                net = pin.get("net", "")
                cands = [(x, y) for (d, x, y) in net_pads.get(net, [])
                         if d != comp["designator"]] if net else []
                if not cands:
                    continue
                dx = -pin["dx"] if mirror else pin["dx"]
                ox, oy = _rotate_offset(dx, pin["dy"], rotation)
                px, py = comp["x"] + ox, comp["y"] + oy
                if len(cands) > PLANE_NET_PAD_COUNT:
                    value = min(math.dist((px, py), c) for c in cands)
                    metric = "nearest_return_mils"
                else:
                    value = _mst_length(cands + [(px, py)])
                    metric = "net_airline_mils"
                total += value
                detail.append({"pin": pin["name"], "net": net, metric: round(value, 1)})
            return (total, detail) if detail else None

        current = comp.get("rotation", 0) % 360
        cur = score(current)
        if cur is None:
            continue
        checked += 1

        best_rot, best = current, cur
        for r in (0, 90, 180, 270):
            s = score(r)
            if s is not None and s[0] < best[0]:
                best_rot, best = r, s

        improvement = cur[0] - best[0]
        if best_rot != current and improvement >= min_improvement_mils:
            suggestions.append({
                "designator": comp["designator"],
                "current_rotation": current,
                "suggested_rotation": best_rot,
                "improvement_mils": round(improvement, 1),
                "bbox_changes": (best_rot - current) % 180 != 0,
                "current_pads": cur[1],
                "suggested_pads": best[1],
            })

    suggestions.sort(key=lambda s: -s["improvement_mils"])
    logger.info(f"Orientation check: {len(suggestions)} suggestions from {checked} components")
    return json.dumps({
        "checked_count": checked,
        "min_improvement_mils": min_improvement_mils,
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
    }, indent=2)

@mcp.tool()
async def check_placement(ctx: Context, cmp_designators: list = None, clearance_mils: float = 6) -> str:
    """
    Verify component placement: find overlaps and clearance violations.

    Checks each target component against every other component on the same
    side of the board. Bounding boxes (which include silkscreen) are used as
    a fast prefilter; close pairs are then measured precisely with Altium's
    primitive-to-primitive distance, so reported distances are true minimum
    distances between any primitives (pads, silk, etc.) of the two parts.

    Run this after placing components - a screenshot is not verification.

    Args:
        cmp_designators (list, optional): Components to check (e.g. ["U12", "R42"]).
            Omit to check the components currently selected in Altium.
        clearance_mils (float): Minimum allowed primitive-to-primitive distance
            in mils (default 6). Pairs closer than this are reported.

    Returns:
        str: JSON object with checked_count, violation_count, and a violations
             list. Each violation has a/b designators, type
             ("bounding_box_overlap" = the parts' outlines intersect, or
             "clearance" = distance below threshold), distance_mils (0 =
             touching/overlapping copper or silk), overlap sizes when boxes
             intersect, and the other part's x/y position. An empty violations
             list means the placement is clean at the given clearance.
    """
    logger.info(f"Checking placement (designators={cmp_designators}, clearance={clearance_mils})")

    params = {"clearance_mils": clearance_mils}
    if cmp_designators:
        params["designators"] = cmp_designators

    response = await altium_bridge.execute_command("check_placement", params)

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error checking placement: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to check placement: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Placement check complete: {result.get('violation_count', '?')} violations")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_screenshot(ctx: Context, view_type: str = "pcb", zoom_to: list = None):
    """
    Take a screenshot of the Altium window, returned as viewable image content.

    Args:
        view_type (str): Type of view to capture - 'pcb' or 'sch'
        zoom_to (list, optional): List of component designators (e.g. ["U12", "R42"]).
            PCB view only: Altium zooms to the bounding box of these components
            (plus a margin) before the capture, so the components fill the frame.
            Omit to capture at the current zoom level.

    Returns:
        Image content of the captured window plus a JSON metadata text block
        (window title, size, zoomed component count)
    """
    logger.info(f"Taking screenshot of Altium {view_type} window (zoom_to={zoom_to})")

    try:
        # First, execute the Altium command to ensure the right document type
        # is focused, optionally zooming to the requested components
        params = {"view_type": view_type.lower()}
        if zoom_to:
            params["designators"] = zoom_to
        response = await altium_bridge.execute_command(
            "take_view_screenshot",
            params
        )
        
        # Check for success
        if not response.get("success", False):
            error_msg = response.get("error", "Unknown error")
            logger.error(f"Error focusing {view_type} document: {error_msg}")
            return json.dumps({"success": False, "error": f"Failed to focus the correct document type: {error_msg}"})

        # Altium reports which document ended up focused. If it is not the
        # kind that was asked for (typically: no document of that kind is
        # open), say so instead of returning the wrong editor labelled as
        # the requested view.
        focus_info = response.get("result", {})
        focus_info = focus_info if isinstance(focus_info, dict) else {}
        focused_kind = str(focus_info.get("focused_kind", "") or "").upper()
        focused_document = focus_info.get("focused_document", "")
        wanted_kind = {"pcb": "PCB", "sch": "SCH"}.get(view_type.lower())
        if wanted_kind and focused_kind and focused_kind != wanted_kind:
            logger.error(f"Requested {view_type} view but {focused_kind} document is focused")
            return json.dumps({
                "success": False,
                "error": f"Requested a '{view_type}' view but Altium has a {focused_kind} "
                         f"document focused ({focused_document}). Is a "
                         f"{wanted_kind} document open in the active project?",
                "requested_view_type": view_type,
                "focused_kind": focused_kind,
                "focused_document": focused_document})

        # Run the screenshot capture in a separate thread
        import threading
        import queue
        import datetime
        from PIL import Image
        
        result_queue = queue.Queue()
        
        def capture_screenshot_thread():
            try:
                # Find Altium windows
                altium_windows = []
                altium_fallback_windows = []
                
                def collect_altium_windows(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        
                        # First, look for windows with Altium and .PrjPcb in the title
                        if "Altium" in title and ".PrjPcb" in title:
                            altium_windows.append({
                                "handle": hwnd,
                                "title": title,
                                "class_name": win32gui.GetClassName(hwnd),
                                "rect": win32gui.GetWindowRect(hwnd)
                            })
                        # Collect any window with Altium in the title as fallback
                        elif "Altium" in title:
                            altium_fallback_windows.append({
                                "handle": hwnd,
                                "title": title,
                                "class_name": win32gui.GetClassName(hwnd),
                                "rect": win32gui.GetWindowRect(hwnd)
                            })
                    return True
                
                win32gui.EnumWindows(collect_altium_windows, 0)
                
                # If no specific Altium .PrjPcb windows found, use the fallback
                if not altium_windows and altium_fallback_windows:
                    altium_windows = altium_fallback_windows
                
                if not altium_windows:
                    result_queue.put({
                        "success": False, 
                        "error": f"No Altium windows found for {view_type} view"
                    })
                    return
                
                # Altium owns several top-level windows with its name in the
                # title, and while it switches documents a small transient one
                # can come first. Take the largest window that is not
                # minimized: that is the main frame.
                def area(w):
                    l, t, r, b = w["rect"]
                    return max(0, r - l) * max(0, b - t)
                candidates = [w for w in altium_windows if not win32gui.IsIconic(w["handle"])]
                window = max(candidates or altium_windows, key=area)
                hwnd = window["handle"]
                
                # Bring Altium to the front and let it paint. Altium only
                # renders a schematic view once it has actually been shown on
                # screen, so a capture taken while it sits behind another
                # window comes back with a blank canvas. Windows may refuse
                # SetForegroundWindow from a process that neither is nor was
                # started by the foreground process; a synthetic Alt press
                # before the call is the standard unlock and is harmless.
                activated = False
                altium_pid = win32process.GetWindowThreadProcessId(hwnd)[1]

                def altium_is_foreground():
                    # Altium owns several top-level windows; any of them
                    # being foreground means Altium is in front.
                    fg = win32gui.GetForegroundWindow()
                    return bool(fg) and win32process.GetWindowThreadProcessId(fg)[1] == altium_pid

                try:
                    for attempt in range(2):
                        if attempt:
                            import ctypes
                            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
                        try:
                            win32gui.SetForegroundWindow(hwnd)
                        except Exception:
                            pass
                        deadline = time.time() + 1.5
                        while time.time() < deadline:
                            time.sleep(0.1)
                            if altium_is_foreground():
                                activated = True
                                break
                        if activated:
                            break
                except Exception as e:
                    logger.warning(f"Could not bring window to foreground: {e}")
                if not activated:
                    logger.warning("Altium is not the foreground window; the capture may show an unpainted view")
                # A freshly switched-to document needs a moment to render.
                time.sleep(1.0)

                # Measure AFTER activation: restoring or switching can change
                # the frame's size.
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width = right - left
                height = bottom - top
                if width < 200 or height < 150:
                    result_queue.put({"success": False, "error": f"Altium window is too small to capture ({width}x{height}); is it minimized?"})
                    return
                
                # Take screenshot using GDI functions instead of ImageGrab
                try:
                    # Get device context
                    hwndDC = win32gui.GetWindowDC(hwnd)
                    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
                    saveDC = mfcDC.CreateCompatibleDC()
                    
                    # Create a bitmap object
                    saveBitMap = win32ui.CreateBitmap()
                    saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
                    saveDC.SelectObject(saveBitMap)
                    
                    # Copy the screen into the bitmap
                    saveDC.BitBlt((0, 0), (width, height), mfcDC, (0, 0), win32con.SRCCOPY)
                    
                    # Convert the bitmap to an Image
                    bmpinfo = saveBitMap.GetInfo()
                    bmpstr = saveBitMap.GetBitmapBits(True)
                    img = Image.frombuffer(
                        'RGB',
                        (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                        bmpstr, 'raw', 'BGRX', 0, 1)
                    
                    # Save a local copy of the screenshot for debugging (non-fatal if it fails)
                    try:
                        debug_filename = str(MCP_DIR / f"screenshot_{view_type}.png")
                        img.save(debug_filename)
                        logger.info(f"Saved debug screenshot to {debug_filename}")
                    except Exception as save_error:
                        logger.warning(f"Could not save debug screenshot to {debug_filename}: {save_error}")
                        debug_filename = None  # Clear it since save failed
                    
                    # Clean up GDI resources
                    win32gui.DeleteObject(saveBitMap.GetHandle())
                    saveDC.DeleteDC()
                    mfcDC.DeleteDC()
                    win32gui.ReleaseDC(hwnd, hwndDC)
                    
                    # Convert to base64
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    buffer.seek(0)
                    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
                    
                    # Put result in queue
                    result_queue.put({
                        "success": True,
                        "width": width,
                        "height": height,
                        "window_title": window["title"],
                        "window_class": window["class_name"],
                        "view_type": view_type,
                        "foreground_confirmed": activated,
                        "image_format": "PNG",
                        "encoding": "base64",
                        "debug_file": debug_filename,
                        "image_data": img_base64
                    })
                    
                except Exception as e:
                    import traceback
                    trace = traceback.format_exc()
                    logger.error(f"GDI screenshot error: {e}\n{trace}")
                    result_queue.put({
                        "success": False, 
                        "error": f"GDI screenshot failed: {str(e)}",
                        "traceback": trace
                    })
                
            except Exception as e:
                import traceback
                result_queue.put({
                    "success": False, 
                    "error": f"Screenshot thread error: {str(e)}",
                    "traceback": traceback.format_exc()
                })
        
        # Start the thread
        thread = threading.Thread(target=capture_screenshot_thread)
        thread.daemon = True
        thread.start()
        
        # Wait for the thread to complete
        thread.join(timeout=10)  # 10 second timeout
        
        if thread.is_alive():
            logger.error("Screenshot thread timed out")
            return json.dumps({"success": False, "error": "Screenshot operation timed out"})
        
        # Get the result from the queue
        if result_queue.empty():
            logger.error("Screenshot thread did not return a result")
            return json.dumps({"success": False, "error": "Screenshot thread did not return a result"})
        
        result = result_queue.get()

        if not result.get("success", False):
            error_msg = result.get("error", "Unknown error")
            logger.error(f"Screenshot error: {error_msg}")
            return json.dumps({"success": False, "error": error_msg})

        logger.info(f"Screenshot taken successfully, size: {result['width']}x{result['height']}")

        # Return the PNG as a proper MCP image content block instead of inline
        # base64 text: raw base64 in the text result exceeds client token
        # limits (a full-window capture is ~300 KB), while image blocks are
        # rendered natively by clients
        image_base64 = result.pop("image_data")
        result.pop("encoding", None)
        zoom_info = response.get("result", {})
        if isinstance(zoom_info, dict) and "zoomed_component_count" in zoom_info:
            result["zoomed_component_count"] = zoom_info["zoomed_component_count"]
        # What was actually captured, as opposed to what was requested
        result["captured_kind"] = focused_kind or None
        result["captured_document"] = focused_document or None
        return [
            json.dumps(result),
            MCPImage(data=base64.b64decode(image_base64), format="png"),
        ]

    except Exception as e:
        logger.error(f"Error in screenshot function: {str(e)}")
        return json.dumps({"success": False, "error": f"Failed to take screenshot: {str(e)}"})
    
@mcp.tool()
async def layout_duplicator(ctx: Context) -> str:
    """
    First step of layout duplication. Selects source components and returns data to match with destination components.
    
    Returns:
        str: JSON object with source and destination component data for matching
    """
    logger.info("Starting layout duplication - selection phase")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "layout_duplicator", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error in layout duplication selection: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to start layout duplication: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", {})
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"success": False, "error": "No component data returned from Altium"})
    
    # Parse the result to check if no source components were selected
    try:
        if isinstance(components_data, str):
            result_json = json.loads(components_data)
            if not result_json.get("success", True):
                logger.info(f"Source component selection issue: {result_json.get('message', 'Unknown issue')}")
                return json.dumps(result_json)
    except Exception as e:
        logger.error(f"Error parsing layout duplicator result: {e}")
    
    logger.info(f"Retrieved layout duplicator component data")
    return json.dumps(components_data, indent=2)

@mcp.tool()
async def layout_duplicator_apply(ctx: Context, source_designators: list, destination_designators: list) -> str:
    """
    Second step of layout duplication. Applies the layout of source components to destination components.
    
    Args:
        source_designators (list): List of source component designators (e.g., ["R1", "C5", "U3"])
        destination_designators (list): List of destination component designators (e.g., ["R10", "C15", "U7"])
    
    Returns:
        str: JSON object with the result of the layout duplication
    """
    logger.info(f"Applying layout duplication from {source_designators} to {destination_designators}")
    
    # Execute the command in Altium to apply layout duplication
    response = await altium_bridge.execute_command(
        "layout_duplicator_apply",
        {
            "source_designators": source_designators,
            "destination_designators": destination_designators
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error applying layout duplication: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to apply layout duplication: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Layout duplication applied successfully")
    return json.dumps(result, indent=2)
    
@mcp.tool()
async def get_pcb_rules(ctx: Context) -> str:
    """
    Get all design rules from the current Altium PCB
    
    Returns:
        str: JSON array of PCB design rules with their properties
    """
    logger.info("Getting PCB design rules")
    
    # Execute the command in Altium to get rule data
    response = await altium_bridge.execute_command(
        "get_pcb_rules",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB rules: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB rules: {error_msg}"})
    
    # Get the rules data
    rules_data = response.get("result", [])
    
    if not rules_data:
        logger.info("No PCB rules found")
        return json.dumps({"message": "No PCB rules found in the current document"})
    
    logger.info(f"Retrieved PCB rules data")
    return json.dumps(rules_data, indent=2)

@mcp.tool()
async def get_pcb_layer_stackup(ctx: Context) -> str:
    """
    Get the detailed layer stackup information from the current Altium PCB including
    copper thickness, dielectric materials, constants, and heights
    
    Returns:
        str: JSON object with detailed layer stackup information
    """
    logger.info("Getting PCB layer stackup information")
    
    # Execute the command in Altium to get layer stackup data
    response = await altium_bridge.execute_command(
        "get_pcb_layer_stackup",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB layer stackup: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB layer stackup: {error_msg}"})
    
    # Get the stackup data
    stackup_data = response.get("result", {})
    
    if not stackup_data:
        logger.info("No PCB layer stackup found")
        return json.dumps({"message": "No PCB layer stackup found in the current document"})
    
    logger.info(f"Retrieved PCB layer stackup data")
    return json.dumps(stackup_data, indent=2)

@mcp.tool()
async def get_output_job_containers(ctx: Context, outjob_path: str = "") -> str:
    """
    Get all available output job containers from a specified OutJob file
    
    Args:
        outjob_path (str): Full path of the .OutJob. Optional: without it the
            first OutJob of any open project is used, which is ambiguous when
            several projects are open.
    
    Returns:
        str: JSON array with all output job containers and their properties
    """
    logger.info(f"Getting output job containers from {outjob_path or 'the first open OutJob'}")

    response = await altium_bridge.execute_command(
        "get_output_job_containers",
        {"outjob_path": outjob_path} if outjob_path else {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting output job containers: {error_msg}")
        return json.dumps({"error": f"Failed to get output job containers: {error_msg}"})
    
    # Get the containers data
    containers_data = response.get("result", [])
    
    if not containers_data:
        logger.info("No output job containers found")
        return json.dumps({"message": "No output job containers found"})
    
    logger.info(f"Retrieved output job containers data")
    return containers_data  # Already in JSON format

@mcp.tool()
async def run_output_jobs(ctx: Context, container_names: list, outjob_path: str = "") -> str:
    """
    Run specified output job containers
    
    Args:
        container_names (list): List of container names to run
        outjob_path (str): Full path of the .OutJob. Optional: without it the
            first OutJob of any open project is used, which is ambiguous when
            several projects are open.
    
    Returns:
        str: JSON object with results of running the output jobs
    """
    logger.info(f"Running output jobs from {outjob_path or 'the first open OutJob'}")
    logger.info(f"Containers to run: {container_names}")

    params = {"container_names": container_names}
    if outjob_path:
        params["outjob_path"] = outjob_path
    response = await altium_bridge.execute_command("run_output_jobs", params)
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error running output jobs: {error_msg}")
        return json.dumps({"error": f"Failed to run output jobs: {error_msg}"})
    
    # Get the result data
    result_data = response.get("result", {})
    
    logger.info(f"Output jobs execution completed")
    
    # If result_data is a string, it's already in JSON format
    if isinstance(result_data, str):
        return result_data
    
    # Otherwise, convert to JSON
    return json.dumps(result_data, indent=2)

@mcp.tool()
async def export_pcb_step(ctx: Context, project_path: str, pcb_path: str, output_path: str,
                          variant: str = "[No Variations]",
                          include_extruded_bodies: bool = False) -> str:
    """
    Export one saved PCB and assembly variant as a populated STEP model.

    Creates an isolated export project and dedicated STEP-only OutJob, then
    runs them through Altium. The export project preserves the source variants
    and settings, with absolute references to its existing documents. Source project, PCB and existing OutJobs are not saved or
    modified. Unsaved source documents and existing output files are refused.
    All components and holes are requested, with separate component bodies.
    Success requires one fresh, complete STEP file and unchanged source hashes.

    Args:
        project_path: Absolute local path to the .PrjPcb containing the PCB.
        pcb_path: Absolute local path to that project's .PcbDoc (not a panel).
        output_path: New absolute .step or .stp path; parent must already exist.
        variant: Exact assembly variant name, or "[No Variations]" for the base design.
        include_extruded_bodies: Request both extruded bodies and STEP models (may overlap).

    Returns:
        JSON with success, output size/hash, source hashes and diagnostic OutJob
        directory. The directory is retained on failure. An export transport
        timeout is a failure even if Altium finishes writing a file later.
    """
    logger.info(f"Exporting STEP from {pcb_path}, variant {variant}, to {output_path}")
    result = await export_step(altium_bridge.execute_command, project_path, pcb_path,
                               output_path, variant, include_extruded_bodies)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
async def create_pcb_footprint(ctx: Context, footprint_name: str, description: str, pads: list, courtyard_x_mm: float = 0, courtyard_y_mm: float = 0) -> str:
    """
    Create a new PCB footprint in the currently active PcbLib document.
    The PcbLib (e.g. Discrete.PcbLib) must be the focused document in Altium.

    Pad format: each element is "pad_number|x_mm|y_mm|width_mm|height_mm|shape"
                shape options: Rect (default), Round, Oval
                Coordinates are in mm relative to component origin (0,0).
                Pin 1 is indicated by a gap in the top-left silkscreen corner.

    Courtyard & silkscreen are auto-generated from pad extents + 0.25 mm margin
    unless courtyard_x_mm / courtyard_y_mm are provided explicitly (half-dimensions).

    Args:
        footprint_name (str): Footprint name as it will appear in the library
        description (str): Description string
        pads (list): List of pad definitions, e.g. ["1|-0.9|0.55|1.0|0.8|Rect", ...]
        courtyard_x_mm (float): Half-width of courtyard in mm (0 = auto)
        courtyard_y_mm (float): Half-height of courtyard in mm (0 = auto)

    Returns:
        str: JSON object with result
    """
    logger.info(f"Creating PCB footprint: {footprint_name} with {len(pads)} pads")

    response = await altium_bridge.execute_command(
        "create_pcb_footprint",
        {
            "footprint_name": footprint_name,
            "description": description,
            "pads": pads,
            "courtyard_x_mm": courtyard_x_mm,
            "courtyard_y_mm": courtyard_y_mm,
        }
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating footprint: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create footprint: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Footprint {footprint_name} created successfully")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_server_status(ctx: Context) -> str:
    """Get the current status of the Altium MCP server"""
    status = {
        "server": "Running",
        "altium_exe": altium_bridge.config.altium_exe_path,
        "script_path": altium_bridge.config.script_path,
        "altium_found": os.path.exists(altium_bridge.config.altium_exe_path),
        "script_found": os.path.exists(altium_bridge.config.script_path),
    }
    
    return json.dumps(status, indent=2)

if __name__ == "__main__":
    logger.info("Starting Altium MCP Server...")
    logger.info(f"Using MCP directory: {MCP_DIR}")
    
    # Initialize the directory
    MCP_DIR.mkdir(exist_ok=True)
    
    # Create the AltiumScript directory if it doesn't exist
    script_dir = MCP_DIR / "AltiumScript"
    script_dir.mkdir(exist_ok=True)
    
    # Verify configuration before starting
    if not altium_bridge.config.verify_paths():
        print("Warning: Configuration not complete. Some functionality may not work.")
    
    # Print status
    print(f"Altium executable: {altium_bridge.config.altium_exe_path}")
    print(f"Script path: {altium_bridge.config.script_path}")
    
    # Run the server
    mcp.run(transport='stdio')