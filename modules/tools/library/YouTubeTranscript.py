from urllib.parse import urlparse, parse_qs

url = args.get('url')
if not url:
    result = "Error: No URL provided."
else:
    parsed_url = urlparse(url)
    video_id = None
    if parsed_url.hostname == 'youtu.be':
        video_id = parsed_url.path[1:]
    elif parsed_url.hostname and ('youtube.com' in parsed_url.hostname):
        if parsed_url.path == '/watch':
            query = parse_qs(parsed_url.query)
            video_id = query.get('v', [None])[0]

    if not video_id:
        result = "Error: Could not extract video ID from URL."
    else:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            transcript_list = YouTubeTranscriptApi().fetch(video_id)
            full_text = " ".join([t.text for t in transcript_list])

            # Truncate if too long to prevent context overflow
            max_length = 8000  # Characters
            if len(full_text) > max_length:
                full_text = full_text[:max_length] + "\n\n[Transcript truncated due to length]"

            result = full_text
        except ImportError:
            result = (
                "Error: youtube_transcript_api is not installed. "
                "Please install it using 'pip install youtube-transcript-api'."
            )
        except Exception as e:
            result = f"Error fetching transcript: {str(e)}"
