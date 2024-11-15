from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_rev_a import LcdCommRevA
from asyncio import run
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
from winrt.windows.storage.streams import DataReader, Buffer, InputStreamOptions
from os import remove, name
from os.path import dirname, abspath, join
import signal
from time import sleep, perf_counter
from library.log import logger
from tempfile import NamedTemporaryFile
from io import BytesIO
from PIL import Image, ImageFilter, ImageDraw, ImageFont
from datetime import datetime

COM_PORT = "AUTO"
REVISION = "A"
stop = False
script_dir = dirname(abspath(__file__))

font_bold = ImageFont.truetype("/res/fonts/roboto/Roboto-Black.ttf", 28)
font_light = ImageFont.truetype("/res/fonts/roboto/Roboto-Medium.ttf", 26)
font_light_small = ImageFont.truetype("/res/fonts/roboto/Roboto-Medium.ttf", 22)

async def get_media_info(retries=3):
    sessions = await MediaManager.request_async()
    current_session = sessions.get_current_session()
    if current_session:
        info = await current_session.try_get_media_properties_async()
        status = current_session.get_playback_info().playback_status.name
        if info:
            for _ in range(retries):
                thumbnail = info.thumbnail
                if thumbnail:
                    return {
                        "title": info.title,
                        "artist": info.artist,
                        "album_title": info.album_title,
                        "thumbnail": thumbnail,
                        "status": status
                    }
            return {
                "title": info.title,
                "artist": info.artist,
                "album_title": info.album_title,
                "thumbnail": None,
                "status": status
            }
    return None

async def read_stream_into_buffer(stream_ref, buffer):
    readable_stream = await stream_ref.open_read_async()
    await readable_stream.read_async(buffer, buffer.capacity, InputStreamOptions.READ_AHEAD)

def get_dominant_and_inverse_color(image, factor=1.2):
    image = image.convert("RGB")
    image = image.resize((100, 100))
    colors = image.getcolors(image.width * image.height)
    dominant_color = max(colors, key=lambda item: item[0])[1]

    r = min(int(dominant_color[0] * factor), 255)
    g = min(int(dominant_color[1] * factor), 255)
    b = min(int(dominant_color[2] * factor), 255)

    brightened_color = (r, g, b)
    inverse_color = (255 - r, 255 - g, 255 - b)

    return brightened_color, inverse_color


def wrap_text(text, font, max_width):
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        current_line.append(word)
        w = font.getlength(' '.join(current_line))
        if w > max_width:
            current_line.pop()
            lines.append(' '.join(current_line))
            current_line = [word]
            
            if len(lines) == 3:
                if current_line or word != words[-1]:
                    last_line = lines[-1]
                    while font.getlength(last_line + "...") > max_width:
                        last_words = last_line.split()
                        last_line = ' '.join(last_words[:-1])
                    lines[-1] = last_line + "..."
                return lines[:3]
    
    if current_line:
        lines.append(' '.join(current_line))
        if len(lines) > 3:
            lines = lines[:2]
            last_line = lines[-1]
            while font.getlength(last_line + "...") > max_width:
                last_words = last_line.split()
                last_line = ' '.join(last_words[:-1])
            lines[-1] = last_line + "..."
            return lines[:3]
    
    return lines

def colored_image(path, width, height, color):
    image = Image.open(path).resize((width, height), Image.LANCZOS).convert("RGBA")
    r, g, b, alpha = image.split()
    colored_image = Image.new("RGBA", image.size, color=color)
    colored_image.putalpha(alpha)
    return colored_image

def save_combined_thumbnail(thumbnail_data=None, title=None, artist=None, album_title=None, status=None):
    screen_width = 480
    screen_height = 320

    time = datetime.now().strftime("%H:%M")

    if thumbnail_data:
        image = Image.open(BytesIO(thumbnail_data))
    else:
        image = Image.open(join(script_dir, "res", "unknown.jpg"))

    brightened_color, inverse_color = get_dominant_and_inverse_color(image)

    blurred = image.copy()
    blurred = blurred.filter(ImageFilter.GaussianBlur(radius=60))

    image_ratio = image.width / image.height
    screen_ratio = screen_width / screen_height

    if image_ratio > screen_ratio:
        new_height = screen_height
        new_width = int(screen_height * image_ratio)
    else:
        new_width = screen_width
        new_height = int(screen_width / image_ratio)

    blurred = blurred.resize((new_width, new_height), Image.LANCZOS)

    dark_overlay = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 40))

    blurred_background = Image.new("RGB", (screen_width, screen_height), (255, 255, 255))
    blurred_background.paste(blurred, ((screen_width - blurred.width) // 2, (screen_height - blurred.height) // 2))
    blurred_background.paste(dark_overlay, (0, 0), dark_overlay)

    target_size = 175

    if image_ratio > 1:
        new_height = target_size
        new_width = int(target_size * image_ratio)
    else:
        new_width = 175
        new_height = int(target_size / image_ratio)

    original = image.resize((new_width, new_height), Image.LANCZOS)

    left = (new_width - target_size) // 2
    top = (new_height - target_size) // 2
    right = left + target_size
    bottom = top + target_size
    cropped_image = original.crop((left, top, right, bottom))

    rounded_mask = Image.new("L", (target_size, target_size), 0)
    draw = ImageDraw.Draw(rounded_mask)
    draw.rounded_rectangle((0, 0, target_size, target_size), 15, fill=255)

    rounded_image = cropped_image.convert("RGBA")
    rounded_image.putalpha(rounded_mask)

    if status == "PAUSED":
        colored_pause = colored_image(join(script_dir, "res", "icons", "pause.png"), 80, 80, inverse_color)
        rounded_image.paste(colored_pause, (45, 45), colored_pause)
        rounded_image.paste(dark_overlay, (0, 0), dark_overlay)

    spotlight = Image.new("RGBA", (325, 325), brightened_color + (100,))

    mask = Image.new("L", (325, 325), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((35, 75, 230, 250), fill=255)

    mask = mask.filter(ImageFilter.GaussianBlur(radius=50))

    spotlight.putalpha(mask)

    blurred_background.paste(spotlight, (-20, -10), spotlight)

    colored_clock = colored_image(join(script_dir, "res", "icons", "clock.png"), 25, 25, inverse_color)

    combined = blurred_background.copy()
    combined.paste(rounded_image, (40, 72), rounded_image)
    combined.paste(colored_clock, (363, 275), colored_clock)

    draw = ImageDraw.Draw(combined)
    max_width = 220

    if title:
        title_lines = wrap_text(title, font_bold, max_width)
        y = 80
        for line in title_lines:
            draw.text((240, y), line, font=font_bold, fill=inverse_color)
            y += 30

    if artist:
        if album_title:
            artist_lines = wrap_text(f"{artist} ({album_title})", font_light, max_width)
        else:
            artist_lines = wrap_text(artist, font_light, max_width)
        y += 10
        for line in artist_lines:
            draw.text((240, y), line, font=font_light, fill=inverse_color)
            y += 30

    draw.text((398, 274), str(time), font=font_light_small, fill=inverse_color)

    with NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
        combined_path = temp_file.name
        combined.save(combined_path, format="PNG")

    return combined_path

if __name__ == "__main__":

    def sighandler(signum, frame):
        global stop
        stop = True

    signal.signal(signal.SIGINT, sighandler)
    signal.signal(signal.SIGTERM, sighandler)
    is_posix = name == 'posix'
    if is_posix:
        signal.signal(signal.SIGQUIT, sighandler)

    lcd_comm = None
    logger.info("Selected Hardware Revision A (Turing Smart Screen 3.5\" & UsbPCMonitor 3.5\"/5\")")
    lcd_comm = LcdCommRevA(com_port=COM_PORT, display_width=320, display_height=480)

    lcd_comm.Reset()

    lcd_comm.InitializeComm()

    lcd_comm.SetBrightness(level=50)

    lcd_comm.SetBackplateLedColor(led_color=(255, 255, 255))

    lcd_comm.SetOrientation(orientation=Orientation.LANDSCAPE)

    background = join(script_dir, "res", "starting.png")

    logger.debug("setting background picture")
    start = perf_counter()
    lcd_comm.DisplayBitmap(background)
    end = perf_counter()
    logger.debug(f"background picture set (took {end - start:.3f} s)")

    while not stop:
        start = perf_counter()
        media_info = run(get_media_info())
        if media_info:

            title = media_info['title']
            artist = media_info['artist']
            album_title = media_info['album_title']
            thumbnail_stream = media_info['thumbnail']
            status = media_info['status']

            if thumbnail_stream:
                thumb_read_buffer = Buffer(5000000)

                run(read_stream_into_buffer(thumbnail_stream, thumb_read_buffer))

                buffer_reader = DataReader.from_buffer(thumb_read_buffer)
                thumbnail_byte_buffer = buffer_reader.read_buffer(thumb_read_buffer.length)
                combined_path = save_combined_thumbnail(thumbnail_byte_buffer, title, artist, album_title, status)
            else:
                combined_path = save_combined_thumbnail(None, title, artist, album_title, status)

        else:
            combined_path = save_combined_thumbnail(None, "No media detected", None, None, status)

        lcd_comm.DisplayBitmap(combined_path)
        remove(combined_path)

        end = perf_counter()
        logger.debug(f"refresh done (took {end - start:.3f} s)")
        sleep(1)

    lcd_comm.closeSerial()