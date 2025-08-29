"""
YouTube downloader utilities based on yt-dlp, with ULTRATHING-style progress.

Features:
- Detect URL type (video, playlist, short, live, channel uploads) using yt-dlp extract_info.
- List available formats (video-only, audio-only, muxed) with sortable metadata.
- Select best quality (by default bestvideo+bestaudio or best audio-only) with codec/fps constraints.
- Download single videos or full playlists.
- Optional subtitles: list, download, and embed (SRT/VTT) if available.
- Progress callback compatible with notebook refreshing progress.

Public API (minimal):
- class YouTubeDownloader
	- probe(url) -> dict: basic info, entries for playlists
	- list_formats(url) -> list[dict]: normalized list of formats
	- download(
		  url,
		  out_dir,
		  video=True,
		  audio=True,
		  subtitles=False,
		  subtitle_langs=None,
		  best_muxed=False,
		  max_height=None,
		  max_fps=None,
		  vcodec_preference=None,
		  acodec_preference=None,
		  progress=None,
		  concurrent=False,
		  playlist_items=None,
	  ) -> list[str]

Notes:
- We rely on yt-dlp which bundles extractors; no API key required.
- Progress callback receives single-line messages; use make_refreshing_progress from mega.py for consistency.
"""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

try:
	import yt_dlp  # type: ignore
except Exception as e:  # pragma: no cover - optional in non-Colab
	yt_dlp = None


# ---- Helpers for formatting / progress ----

def _sizeof_mb(bytes_val: Optional[int]) -> Optional[float]:
	if not bytes_val and bytes_val != 0:
		return None
	return round(bytes_val / (1024 * 1024), 2)


def _progress_hook_factory(progress_cb):
	"""Create a yt-dlp progress hook that emits ULTRATHING-style single line updates."""

	def hook(d):
		if not progress_cb:
			return
		status = d.get('status')
		if status == 'downloading':
			percent = d.get('_percent_str', '').strip()
			speed = d.get('_speed_str', '').replace('MiB/s', 'MB/s').replace('KiB/s', 'KB/s')
			eta = d.get('eta')
			eta_str = f"ETA {int(eta)}s" if eta is not None else "ETA --s"
			total = _sizeof_mb(d.get('total_bytes')) or _sizeof_mb(d.get('total_bytes_estimate'))
			downloaded = _sizeof_mb(d.get('downloaded_bytes'))
			name = d.get('filename') or d.get('info_dict', {}).get('title') or "video"
			base = os.path.basename(str(name))
			# Compose: [scrolling-name pct][bar] downloaded/total MB  speed  ETA
			bar = d.get('_progress_bar', '')
			# yt-dlp doesn't expose raw bar, we synthesize a 20-char bar from percent
			try:
				pc = float(percent.strip('%'))
			except Exception:
				pc = 0.0
			filled = int(pc / 5)  # 20-width
			bar = f"[{'#'*filled}{'.'*(20-filled)}]"
			left = f"{base[:19].ljust(19)} {percent.rjust(4)}"
			sizes = f" {downloaded or 0:.2f}/{total or 0:.2f} MB"
			line = f"{left} {bar} {sizes}  {speed}  {eta_str}"
			progress_cb(line)
		elif status == 'finished':
			filename = d.get('filename') or ''
			progress_cb(f"Zakończono: {os.path.basename(filename)}")

	return hook


@dataclass
class FormatRow:
	format_id: str
	ext: str
	vcodec: str
	acodec: str
	fps: Optional[float]
	height: Optional[int]
	tbr: Optional[float]
	filesize: Optional[int]
	source: str  # video-only / audio-only / muxed

	def to_dict(self) -> Dict[str, Any]:
		return {
			'format_id': self.format_id,
			'ext': self.ext,
			'vcodec': self.vcodec,
			'acodec': self.acodec,
			'fps': self.fps,
			'height': self.height,
			'tbr': self.tbr,
			'filesize': self.filesize,
			'filesize_mb': _sizeof_mb(self.filesize),
			'source': self.source,
		}


class YouTubeDownloader:
	def __init__(self):
		if yt_dlp is None:
			raise RuntimeError("yt-dlp nie jest zainstalowany. Zainstaluj pakiet 'yt-dlp'.")

	# ---- Probing ----
	def probe(self, url: str) -> Dict[str, Any]:
		"""Return basic info; for playlists returns 'entries'."""
		ydl_opts = {'skip_download': True, 'quiet': True, 'extract_flat': 'discard_in_playlist'}
		with yt_dlp.YoutubeDL(ydl_opts) as ydl:
			info = ydl.extract_info(url, download=False)
		return info

	# ---- Formats ----
	def list_formats(self, url: str) -> List[Dict[str, Any]]:
		info = self.probe(url)
		if info.get('_type') == 'playlist':
			# For playlist, list formats of the first item to preview; real download handles each
			entries = info.get('entries') or []
			if not entries:
				return []
			first = entries[0].get('url') or entries[0].get('id')
			info = self.probe(first)

		fmts = []
		for f in info.get('formats', []) or []:
			vcodec = f.get('vcodec') or 'none'
			acodec = f.get('acodec') or 'none'
			source = 'muxed'
			if vcodec != 'none' and acodec == 'none':
				source = 'video-only'
			elif vcodec == 'none' and acodec != 'none':
				source = 'audio-only'
			row = FormatRow(
				format_id=str(f.get('format_id')),
				ext=f.get('ext') or '',
				vcodec=vcodec,
				acodec=acodec,
				fps=f.get('fps'),
				height=f.get('height'),
				tbr=f.get('tbr'),
				filesize=f.get('filesize') or f.get('filesize_approx'),
				source=source,
			)
			fmts.append(row.to_dict())
		# Sort: height desc, fps desc, tbr desc
		fmts.sort(key=lambda r: (r.get('height') or 0, r.get('fps') or 0, r.get('tbr') or 0), reverse=True)
		return fmts

	# ---- Download ----
	def download(
		self,
		url: str,
		out_dir: str,
		video: bool = True,
		audio: bool = True,
		subtitles: bool = False,
		subtitle_langs: Optional[List[str]] = None,
		best_muxed: bool = False,
		max_height: Optional[int] = None,
		max_fps: Optional[int] = None,
		vcodec_preference: Optional[str] = None,
		acodec_preference: Optional[str] = None,
		progress=None,
		concurrent: bool = False,
		playlist_items: Optional[str] = None,
		force_mp4: bool = True,
	strict_mp4_single: bool = False,
	) -> List[str]:
		"""Download video/audio/subtitles. Returns list of output file paths.

		- video/audio flags control whether to fetch bestvideo+bestaudio (with FFmpeg merge) or audio-only.
		- best_muxed=True prefers best "muxed" format instead of merging streams.
		- max_height/fps, *codec_preference narrow selection using format selectors.
		- subtitles: download available subs; subtitle_langs like ['pl','en'] or ['all'].
		- playlist_items: e.g., '1-5,10' to restrict playlist indices.
		"""
		os.makedirs(out_dir, exist_ok=True)

		# Build format selector
		fmt_parts: List[str] = []
		if strict_mp4_single and video and audio:
			# Only already-muxed MP4
			fmt_selector = 'b[ext=mp4]'
		elif video and audio and not best_muxed:
			# Prefer video-only + audio-only, later merged
			vsel = 'bv*'
			asel = 'ba'
			if max_height:
				vsel += f"[height<=?{max_height}]"
			if max_fps:
				vsel += f"[fps<=?{max_fps}]"
			if vcodec_preference:
				vsel += f"[vcodec~=\"({vcodec_preference})\"]"
			elif force_mp4:
				vsel += f"[vcodec~=\"(avc1|h264)\"]"
			if acodec_preference:
				asel += f"[acodec~=\"({acodec_preference})\"]"
			elif force_mp4:
				asel += f"[acodec~=\"(mp4a|aac)\"]"
			# Fallbacks: unconstrained pair, then best mp4 muxed, then best
			v_fallback = 'bv*'
			a_fallback = 'ba'
			pair_primary = f"{vsel}+{asel}"
			pair_fallback = f"{v_fallback}+{a_fallback}"
			mp4_muxed = 'b[ext=mp4]' if force_mp4 else 'best'
			fmt_selector = f"{pair_primary}/{pair_fallback}/{mp4_muxed}/best"
		elif video and audio and best_muxed:
			fmt_selector = 'b[ext=mp4]/best' if force_mp4 else 'best'
		elif video and not audio:
			vsel = 'bv*'
			if max_height:
				vsel += f"[height<=?{max_height}]"
			if max_fps:
				vsel += f"[fps<=?{max_fps}]"
			if vcodec_preference:
				vsel += f"[vcodec~=\"({vcodec_preference})\"]"
			elif force_mp4:
				vsel += f"[vcodec~=\"(avc1|h264)\"]"
			fmt_selector = f"{vsel}/b[ext=mp4]/best" if force_mp4 else f"{vsel}/best"
		elif audio and not video:
			asel = 'bestaudio'
			if acodec_preference:
				asel += f"[acodec~=\"({acodec_preference})\"]"
			elif force_mp4:
				asel += f"[acodec~=\"(mp4a|aac)\"]"
			fmt_selector = asel
		else:
			fmt_selector = 'b[ext=mp4]/best' if force_mp4 else 'best'

		ydl_opts: Dict[str, Any] = {
			'outtmpl': os.path.join(out_dir, '%(title)s [%(id)s].%(ext)s'),
			'noprogress': True,
			'progress_hooks': [_progress_hook_factory(progress)],
			'merge_output_format': None if strict_mp4_single else ('mp4' if force_mp4 else None),
			'postprocessors': [],
			'quiet': True,
			'ignoreerrors': True,
			'concurrent_fragment_downloads': 4 if concurrent else 1,
			'format': fmt_selector,
			'writesubtitles': subtitles,
			'writeautomaticsub': subtitles,
			'subtitleslangs': subtitle_langs or ['pl', 'en'],
			'subtitlesformat': 'srt',
			'playlist_items': playlist_items,
			'keepvideo': False,
			'prefer_free_formats': False if force_mp4 else True,
		}

		# If requesting audio-only, add audio extraction to e.g., m4a
		if audio and not video:
			ydl_opts['postprocessors'].append({
				'key': 'FFmpegExtractAudio',
				'preferredcodec': 'm4a',
				'preferredquality': '0',
			})
		if force_mp4 and not strict_mp4_single:
			# Ensure final container is MP4 when possible
			ydl_opts['postprocessors'].append({
				'key': 'FFmpegVideoRemuxer',
				'preferedformat': 'mp4',
			})
		# If merging streams, yt-dlp will use FFmpeg by default. Nothing else needed.

		outputs: List[str] = []

		def collect_outputs_from_info(info_obj: Dict[str, Any]):
			if not info_obj:
				return
			if info_obj.get('_type') == 'playlist':
				for ent in info_obj.get('entries') or []:
					collect_outputs_from_info(ent or {})
				return
			# For single video
			# requested_downloads entries contain final filepaths
			rds = info_obj.get('requested_downloads') or []
			for rd in rds:
				fp = rd.get('filepath') or rd.get('_filename') or info_obj.get('filepath') or info_obj.get('_filename')
				if fp:
					outputs.append(fp)
			# fallback: filename/file
			for key in ('filepath', '_filename', 'filename'):
				fp = info_obj.get(key)
				if fp:
					outputs.append(fp)

		with yt_dlp.YoutubeDL(ydl_opts) as ydl:
			info = ydl.extract_info(url, download=True)
		collect_outputs_from_info(info if isinstance(info, dict) else {})

		# De-duplicate and filter out temp files
		uniq: List[str] = []
		seen = set()
		for p in outputs:
			if not p:
				continue
			rp = os.path.normcase(os.path.realpath(p))
			if rp in seen:
				continue
			# skip temp parts
			base = os.path.basename(p)
			if base.endswith('.part') or base.endswith('.ytdl'):
				continue
			seen.add(rp)
			uniq.append(p)
		return uniq


__all__ = [
	'YouTubeDownloader',
]

