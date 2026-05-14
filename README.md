This is my first time working with docker, follow along or feel free to fork as I figure this out!



  compose.yml

    services:
      novamp3:
        image: novaleg/novamp3v0.2:latest

        container_name: novaplayer

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


Currently testing and finding bugs for use cases where the library is large, if at any point you discover that your cover art is incorrect, open an issue. For now a temporary fix is to:

    docker exec -it novaplayer bash

    cd data

    rm -fr covers && rm -fr music.db

    exit

    docker compose restart

This will remove any incorectly saved cover art, and re-cache the cover art assignments.
