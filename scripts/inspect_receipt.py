"""Inspect last execution receipt in database on VM."""
import json
import sqlite3

conn = sqlite3.connect("/home/auron/auron/data/auron.db")
c = conn.cursor()
c.execute("SELECT receipt_json FROM attempts ORDER BY created_at DESC LIMIT 1")
row = c.fetchone()
if row and row[0]:
    receipt = json.loads(row[0])
    inspect = receipt.get("container", {}).get("inspect", {})
    hc = inspect.get("HostConfig", {})
    cfg = inspect.get("Config", {})
    print("=== DOCKER INSPECTION EVIDENCE ===")
    print(f"HostConfig.Memory:         {hc.get('Memory')} bytes")
    print(f"HostConfig.NanoCpus:       {hc.get('NanoCpus')}")
    print(f"HostConfig.PidsLimit:      {hc.get('PidsLimit')}")
    print(f"HostConfig.NetworkMode:    {hc.get('NetworkMode')}")
    print(f"HostConfig.ReadonlyRootfs: {hc.get('ReadonlyRootfs')}")
    print(f"HostConfig.CapDrop:        {hc.get('CapDrop')}")
    print(f"HostConfig.SecurityOpt:    {hc.get('SecurityOpt')}")
    print(f"Config.User:               {cfg.get('User')}")
    print(f"Mounts count:              {len(inspect.get('Mounts', []))}")
    for m in inspect.get("Mounts", []):
        print(f"  Mount: Type={m.get('Type')} Dest={m.get('Destination')} RW={m.get('RW')} Mode={m.get('Mode')}")
else:
    print("No execution attempts found in database!")
