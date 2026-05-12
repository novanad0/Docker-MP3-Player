This is my first time working with docker, follow along or feel free to fork as I figure this out!



  compose.yml

    services:

      mp3-player:

      image: novaleg/novamp3:latest

      container_name: mp3-player

      ports:
        - "8000:8000"

      volumes:

        # User music library
        - /path/to/music:/app/music
        # Example
        # /mnt/media/music:/app/music

        # Persistent cache/database
        - ./data:/app/data

      restart: unless-stopped
