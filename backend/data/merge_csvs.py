import pandas as pd
import glob

# Find every CSV in the map_data folder
files = glob.glob("map_data/*.csv")
print(f"Found {len(files)} files:")
for f in files:
    print(" -", f)

# Read each one into a table
frames = [pd.read_csv(f) for f in files]

# Check they all have the same columns before combining
first_cols = set(frames[0].columns)
for f, df in zip(files, frames):
    if set(df.columns) != first_cols:
        print(f"WARNING: {f} has different columns!")

# Stack them on top of each other into one big table
merged = pd.concat(frames, ignore_index=True)
print(f"\nTotal rows: {len(merged)}")
print(merged["LayerCategory"].value_counts())

# Save the result
merged.to_csv("board_records_merged.csv", index=False)
print("\nSaved as board_records_merged.csv ✅")