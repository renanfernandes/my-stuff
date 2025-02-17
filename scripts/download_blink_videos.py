#!/usr/bin/env python3
import os
import asyncio
import requests
from blinkpy import Blink

# --- User Configuration ---
USERNAME = ""      # Replace with your Blink username/email
PASSWORD = ""      # Replace with your Blink password
REGION   = "US"                       # Change if you’re in another region (e.g., "EU")
SAVE_DIR = "blink_videos"             # Directory to save downloaded videos

# --- Setup local directory ---
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

async def main():
    print("Initializing Blink system...")
    blink = Blink()
    blink.config.username = USERNAME
    blink.config.password = PASSWORD
    blink.config.region = REGION

    # Start the Blink system (login & session setup)
    await blink.start()

    print("Refreshing video list from Blink...")
    await blink.refresh(videos=True)

    # Check if videos are available
    if not blink.videos or "videos" not in blink.videos:
        print("No videos found. Ensure your Blink system has stored videos available.")
        return

    videos = blink.videos["videos"]
    print(f"Found {len(videos)} video(s). Starting download...")

    # Download each video
    for video in videos:
        video_url = video.get("video")
        created_at = video.get("created_at", "unknown_time").replace(":", "-")  # Avoid issues with colons in filenames
        video_id = video.get("id", "unknown_id")
        filename = f"{created_at}_{video_id}.mp4"
        filepath = os.path.join(SAVE_DIR, filename)

        if not video_url:
            print(f"Skipping video {video_id}: No URL found")
            continue

        print(f"Downloading {filename} ...")
        try:
            response = requests.get(video_url, stream=True)
            response.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            print(f"Downloaded {filename}")
        except Exception as e:
            print(f"Failed to download video {video_id}. Error: {e}")

    print("All downloads complete.")

if __name__ == '__main__':
    asyncio.run(main())