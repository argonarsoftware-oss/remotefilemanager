import ctypes
import os

bitmask = ctypes.windll.kernel32.GetLogicalDrives()
for i in range(26):
    if bitmask & (1 << i):
        letter = chr(65 + i)
        drive = letter + ":"  + "\\"
        exists = os.path.exists(drive)
        print(f"  {drive}  exists={exists}")
