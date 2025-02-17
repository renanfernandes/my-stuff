import asyncio
import os
import aiohttp
from blinkpy.blinkpy import Blink
from blinkpy.auth import Auth

# --- User Configuration ---
USERNAME = ""      # Replace with your Blink username/email
PASSWORD = ""      # Replace with your Blink password
SAVE_DIR = "blink_videos"   # Directory where videos will be stored

async def download_video(session, video_url, filepath):
    async with session.get(video_url) as response:
        response.raise_for_status()  # Raise an exception for non-200 responses
        with open(filepath, "wb") as f:
            while True:
                chunk = await response.content.read(8192)
                if not chunk:
                    break
                f.write(chunk)
    print(f"Downloaded: {filepath}")

async def main():
    # Ensure the save directory exists.
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)

    # Create a single aiohttp session for both Blink and downloads.
    async with aiohttp.ClientSession() as session:
        # Initialize Blink with the session.
        blink = Blink(session=session)
        auth = Auth({"username": USERNAME, "password": PASSWORD}, no_prompt=False)
        blink.auth = auth

        # Connect and authenticate with Blink (MFA prompt will appear if needed).
        await blink.start()

        # Refresh data (videos, cameras, etc.). Do not pass any extra arguments.
        await blink.refresh()

        # Retrieve videos. In many BlinkPy versions, videos are stored as a dictionary under "videos".
        videos = blink.videos.get("videos", [])
        if not videos:
            print("No videos found!")
            return

        print(f"Found {len(videos)} video(s). Starting download...")

        # Loop over each video and download it.
        for video in videos:
            video_url = video.get("video")
            if not video_url:
                print("Skipping a video; no URL found.")
                continue

            # Generate a filename using the video's creation time and ID.
            created_at = video.get("created_at", "unknown_time").replace(":", "-")
            video_id = video.get("id", "unknown_id")
            filename = f"{created_at}_{video_id}.mp4"
            filepath = os.path.join(SAVE_DIR, filename)

            try:
                await download_video(session, video_url, filepath)
            except Exception as e:
                print(f"Error downloading {filename}: {e}")

        print("All downloads complete.")

# Run the asynchronous main() function.
asyncio.run(main())