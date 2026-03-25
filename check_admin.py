import ctypes
is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
print(f"Running as Admin: {is_admin}")
