"""Read visible table cells, including cropped tables and uploaded contact evidence."""
import csv
import io
import re
import subprocess
from PIL import ImageOps, ImageDraw
from research import phones
from source_audit import email_occurrences


def word_lines(words):
    lines = []
    for w in sorted(words, key=lambda w: (int(w['top']), int(w['left']))):
        y = int(w['top']) + int(w['height']) / 2
        target = next((line for line in reversed(lines[-8:])
                       if abs(line[0] - y) < max(10, int(w['height']) * .65)), None)
        if target:
            target[1].append(w)
        else:
            lines.append([y, [w]])
    return [(y, sorted(ws, key=lambda w: int(w['left']))) for y, ws in sorted(lines)]


def header(words):
    aliases = {'name': 'visible_name', 'job': 'role', 'title': 'role', 'company': 'company',
               'email': 'email', 'emails': 'email', 'phone': 'phone', 'phones': 'phone'}
    for y, ws in word_lines(words):
        columns = {}
        for w in ws:
            key = aliases.get(w['text'].lower().strip(':'))
            if key and key not in columns:
                columns[key] = int(w['left'])
        if 'visible_name' in columns and (('role' in columns and 'company' in columns) or 'email' in columns):
            return y, columns
    return None


def run_ocr(image, command, mode):
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    result = subprocess.run([command, 'stdin', 'stdout', '--psm', str(mode), 'tsv'],
                            input=buf.getvalue(), capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[:300])
    return [w for w in csv.DictReader(io.StringIO(result.stdout.decode()), delimiter='\t') if w.get('text', '').strip()]


def name_image(crop):
    """Remove gray privacy masks, retaining only pixels to their left."""
    gray = crop.convert('L')
    dark = gray.resize((1, 1)).getpixel((0, 0)) < 128
    result = ImageOps.invert(gray) if dark else gray.copy()
    result = result.point(lambda p: 0 if p < 128 else 255)
    if dark:
        pixels = crop.convert('RGB').load()
        draw = ImageDraw.Draw(result)
        bands = []
        for y in range(crop.height):
            start = None
            for x in range(crop.width):
                r, g, b = pixels[x, y]
                masked = 85 <= r <= 125 and max(r, g, b) - min(r, g, b) < 8
                if masked:
                    if start is None: start = x
                    if x - start >= 55:
                        if bands and y - bands[-1][1] < 20:
                            bands[-1][1] = y
                            bands[-1][2] = min(bands[-1][2], start)
                        else:
                            bands.append([y, y, start])
                        break
                else:
                    start = None
        for top, bottom, left in bands:
            draw.rectangle((left, max(0, top-4), crop.width, min(crop.height, bottom+4)), fill=255)
    return result


def extract_words(image, command):
    if image.width * image.height > 25_000_000:
        raise ValueError('Image exceeds 25 megapixels')
    image = ImageOps.exif_transpose(image).convert('RGB')
    scale = min(2, 4000 / max(image.size))
    image = image.resize((int(image.width * scale), int(image.height * scale)))
    words = run_ocr(image, command, 11)
    found = header(words)
    if not found:
        return words
    y, columns = found
    top = int(y + 25)
    refined = [w for w in words if int(w['top']) < top]
    ordered = sorted(columns.items(), key=lambda pair: pair[1])
    for i, (key, left) in enumerate(ordered):
        right = ordered[i+1][1] - 15 if i+1 < len(ordered) else image.width
        if key == 'visible_name' and 'company' not in columns:
            # The frozen name column can be followed by an unlabeled clipped
            # company column. Keep the name crop away from those fragments.
            right = min(right, left + int((right-left) * .60))
        if right <= left: continue
        crop = image.crop((left, top, right, image.height))
        if key == 'visible_name':
            prepared = name_image(crop)
        else:
            prepared = crop.convert('L')
            if prepared.resize((1, 1)).getpixel((0, 0)) < 128:
                prepared = ImageOps.invert(prepared)
        for w in run_ocr(prepared, command, 6):
            if key == 'email' and '@' in w['text'] and int(w['left']) + int(w['width']) >= prepared.width-8:
                w['clipped'] = True
            w['left'] = str(int(w['left']) + left)
            w['top'] = str(int(w['top']) + top)
            w['column'] = key
            refined.append(w)
        crop.close()
        prepared.close()
    return refined


def parse_table(words):
    lines = word_lines(words)
    text = '\n'.join(' '.join(w['text'] for w in ws) for _, ws in lines)
    found = header(words)
    if not found:
        return None, text
    y0, columns = found
    ordered = sorted(columns.items(), key=lambda pair: pair[1])
    rows = []
    for y, ws in lines:
        if y <= y0 + 8: continue
        cells = {key: [] for key in columns}
        for w in ws:
            x = int(w['left'])
            if x < ordered[0][1] - 15: continue
            key = w.get('column')
            if key is None:
                key = next((k for k, left in reversed(ordered) if x >= left-15), None)
            if key in cells: cells[key].append(w['text'])
        values = {k: ' '.join(v).strip(' |') for k, v in cells.items()}
        name = values.get('visible_name', '')
        match = re.match(r"^[^\W\d_]+(?:[ '.-]+[^\W\d_]+)*", name)
        name = match.group().rstrip(' .-') if match else ''
        company = values.get('company', '').strip()
        company = re.sub(r'^[^\w]+', '', company)
        role = values.get('role', '').strip()
        email_text = values.get('email', '')
        clipped = any(w.get('clipped') for w in ws)
        emails = [] if clipped else list(dict.fromkeys(address for address, _ in email_occurrences(email_text)))
        phone_text = values.get('phone', '')
        numbers = phones(phone_text)
        cropped = 'role' not in columns or 'company' not in columns
        if cropped and not (email_text or numbers):continue
        if (not (company and role) and not (cropped and (name or emails))) or re.search(r'^(help|total|page|\d+\s*-)', name, re.I):
            continue
        row = {'visible_name': name, 'role': role, 'company': company,
               'extraction_status': 'REVIEW_CROPPED_COLUMNS' if cropped else 'REVIEW_OCR',
               'email': '\n'.join(emails), 'published_phone_candidates': '\n'.join(numbers),
               'email_text': email_text, 'phone_text': phone_text}
        if cropped:
            row['reason'] = 'Role/company columns are cropped or missing. Enter company before public research.'
        if clipped:
            row['reason'] = (row.get('reason', '') + ' Email text is clipped at the image edge; review the original before using it.').strip()
        if emails or numbers:
            row.update(discovery='VISIBLE_IN_UPLOAD', person_association='NEEDS_REVIEW',
                       mailbox_status='NOT_CHECKED', dns_status='NOT_CHECKED',
                       context=' | '.join(v for v in (name, role, company, email_text, phone_text) if v))
        rows.append(row)
    return rows, text
