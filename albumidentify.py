#!/usr/bin/python2.5
import sys
import fingerprint
import musicdns
import os
import lookups
import parsemp3
import musicbrainz2
import itertools
import pickle
import md5
import random
import shelve

def output_list(l):
	if not l:
		return "[]"
	l.sort()
	ret=[]
	start=l[0]
	end=l[0]
	for i in l[1:]:
		if end+1==i:
			end=i
			continue
		if start!=end:
			ret.append("%d-%d" % (start,end))
		else:
			ret.append("%d" % start)
		start=i
		end=i
	if start!=end:
		ret.append("%d-%d" % (start,end))
	else:
		ret.append("%d" % start)
	return "[%s]" % (",".join(ret))
		

key = 'a7f6063296c0f1c9b75c7f511861b89b'

def decode(frommp3name, towavname):
        if frommp3name.lower().endswith(".mp3"):
                os.spawnlp(os.P_WAIT,"mpg123","mpg123","--quiet","--wav",
                        towavname,frommp3name)
        elif frommp3name.lower().endswith(".flac"):
                os.spawnlp(os.P_WAIT,"flac","flac","-d", "--totally-silent", "-o", towavname,
                        frommp3name)

fileinfocache=shelve.open(os.path.expanduser("~/.albumidentifycachedb"),"c")

class FingerprintFailed(Exception):
	def __str__(self):
		return "Failed to fingerprint track"

def get_file_info(fname):
	print "identifying",fname
	#sys.stdout.write("identifying "+os.path.basename(fname)+"\r\x1B[K")
	#sys.stdout.flush()
	fhash = md5.md5(open(fname,"r").read()).hexdigest()
	if fhash in fileinfocache:
		return fileinfocache[fhash]
	# While testing this uses a fixed name in /tmp
	# and checks if it exists, and doesn't decode if it does.
	# This is for speed while debugging, should be changed with
	# tmpname later
	toname=os.path.join("/tmp/fingerprint.wav")
	if not os.path.exists(toname):
		sys.stdout.write("decoding"+os.path.basename(fname)+"\r\x1B[K")
		sys.stdout.flush()
		decode(fname,toname)
	sys.stdout.write("Generating fingerprint\r")
	sys.stdout.flush()
	(fp, dur) = fingerprint.fingerprint(toname)
	os.unlink(toname)

	sys.stdout.write("Fetching fingerprint info\r")
	sys.stdout.flush()
	(trackname, artist, puid) = musicdns.lookup_fingerprint(fp, dur, key)
	print "***",`artist`,`trackname`,puid
	if puid is None:
		raise FingerprintFailed()
	sys.stdout.write("Looking up PUID\r")
	sys.stdout.flush()
	tracks = lookups.get_tracks_by_puid(puid)
	data=(fname,artist,trackname,dur,tracks,puid)
	if tracks!=[]:
		fileinfocache[fhash]=data
	else:
		print "Musicbrainz doesn't know about this track, not caching"
	return data

def get_dir_info(dirname):
	files=os.listdir(dirname)
	files.sort()
	tracknum=0
	trackinfo={}
	for i in files:
		if not (i.lower().endswith(".mp3") or i.lower().endswith(".flac")):
			print "Skipping non mp3/flac file",`i`
			continue
		tracknum=tracknum+1
		fname=os.path.join(dirname,i)
		trackinfo[tracknum]=get_file_info(fname)
	return trackinfo

def find_more_tracks(tracks):
	# There is a n:n mapping of puid's to tracks.
	# All puid's that match a track should be the same song.
	# Thus if PUIDa maps to TrackA, which also has PUIDb
	# and PUIDb maps to TrackB too, then PUIDa should map to
	# TrackB too...
	tracks=tracks[:]
	donetracks=[]
	donepuids=[]
	donetrackids=[]

	while tracks!=[]:
		t=tracks.pop()
		donetracks.append(t)
		yield t
		newt = lookups.get_track_by_id(t.id)
		for p in newt.puids:
			if p in donepuids:
				continue
			donepuids.append(p)
			ts = lookups.get_tracks_by_puid(p)
			for u in ts:
				if u.id in donetrackids:
					continue
				tracks.append(u)
				donetrackids.append(u.id)
					
def find_even_more_tracks(fname,tracknum,possible_releases):
	gotone = False
        if fname.lower().endswith(".flac"):
                return
	mp3data = parsemp3.parsemp3(fname)
	if "TIT2" not in mp3data["v2"]:
		return
	ftrackname = mp3data["v2"]["TIT2"]
	for (rid,v) in possible_releases.items():
		release = lookups.get_release_by_releaseid(rid)
		rtrackname = release.tracks[tracknum-1].title
		if rtrackname.lower() == ftrackname.lower():
			yield release.tracks[tracknum-1]


def create_track_generator(trackinfo, possible_releases):
	"""Identify all conceivable track IDs for each track.

	Args:
		trackinfo: A dictionary of the form:
			<tracknum> => (fname,artist,trackname,dur,[mbtrackids])
		possible_releases: Dictionary containing releases under consideration.

	Returns:
		A dictionary, indexed by tracknum containing an iterator that will
		yield every conceivable track object for the identified track.
	"""
	track_generator={}
	for tracknum, details in trackinfo.iteritems():
		fname, artist, trackname, dur, trackids, puid = details
		track_generator[tracknum]=itertools.chain(
					(track
						for track in trackids),
					find_more_tracks([
						track for track in trackids]),
					find_even_more_tracks(fname,
							tracknum,
							possible_releases))
	return track_generator


def find_releases_for_tracks(trackinfo):
	"""Find all potential release this album matches.

	Args:
		trackinfo: A dictionary of the form:
			<tracknum> => (fname,artist,trackname,dur,[mbtrackids])

	Yields:
		A list of possible release ids
		
		This version works by trying a breadth first search of releases to try
		and avoid wasting a lot of time finding releases which are going to
		be ignored.
	"""
	possible_releases = {}
	impossible_releases = []
	completed_releases = []
	exhausted_tracks = []
	track_generator = create_track_generator(trackinfo, possible_releases)
	tracks = track_generator.keys()

	# Begin a breadth first search over all the tracks.
	iteration = 1
	while 1:
		print "** Beginning iteration %d" % iteration
		num_processed = 0
		for tracknum in sorted(tracks):
			if tracknum in exhausted_tracks:
				continue
			# Get the next possibility for this track.
			try:
				track = track_generator[tracknum].next()
				num_processed += 1
			except StopIteration, si:
				# No more possibilities for this track.
				print
				print "All possibilities for track", tracknum, "exhausted"
				print "puid:", trackinfo[tracknum][5]
				exhausted_tracks.append(tracknum)

				# Any release not already containing this track is now invalid
				# as there is no further chance for it to get it.
				for relid in possible_releases.keys():
					if tracknum not in possible_releases[relid]:
						release = lookups.get_release_by_releaseid(releaseid)
						print "Removing from consideration: %s - %s" % (
								release.artist.name, release.title)
						print " does not contain this track!"
						if tracknum-1 in release.tracks:
							print " track %s on release: %s" % (
									release.tracks[tracknum-1].id)
						print " matched tracks: %s" % (
								output_list(possible_releases[relid]))
						del possible_releases[relid]	

				# If there are no more possible releases then exit now.
				if not possible_releases:
					print "Sorry, This leaves no more possible releases!"
					return
				else:
					continue

			# Iterate through every release this track is associated with.
			for releaseid in (x.id for x in track.releases):
				# Early jump if we already know this release is no good.
				if releaseid in impossible_releases:
					continue
				# Early jump if this release is already good.
				if releaseid in completed_releases:
					continue
				# Early jump if this track has already been matched to this
				# release.
				if releaseid in possible_releases:
					if tracknum in possible_releases[releaseid]:
						continue
				
				# Get release details.
				release = lookups.get_release_by_releaseid(releaseid)

				# Check release matches number of tracks we are expecting.
				if len(release.tracks) != len(trackinfo):
					# Ignore release -- wrong number of tracks
					print "Removing from consideration: %s - %s" % (
							release.artist.name, release.title)
					print " track count %d did not match expected: %d" % (
							len(release.tracks), len(trackinfo))
					impossible_releases.append(releaseid)
					continue

				if releaseid not in possible_releases:
					print "Adding to consideration: %s - %s" % (
							release.artist.name, release.title)
					possible_releases[releaseid] = []
				
				if tracknum not in possible_releases[releaseid]:
					print "Matched track %d to %s - %s (tracks found: %s)" % (
							tracknum, release.artist.name, release.title, 
							output_list(possible_releases[releaseid]))
					possible_releases[releaseid].append(tracknum)

				# Check if this track has validated a lease.
				if len(possible_releases[releaseid]) == len(trackinfo):
					print "Valid Release Found: %s - %s" % (
							release.artist.name, release.title)
					completed_releases.append(releaseid)
					yield releaseid
			# End of release loop.
		# End of track loop.
		if not num_processed:
			# All tracks have exhaused their possibilities
			break
		print "Releases under consideration:"
		for p_relid in possible_releases:
			p_release = lookups.get_release_by_releaseid(p_relid)
			print " %s - %s (tracks found: %s)" % (
					p_release.artist.name, p_release.title,
					output_list(possible_releases[p_relid]))
		iteration += 1
	# End of processing loop.
	if not completed_releases:
		print "Unable to find any matching releases!"


def guess_album(trackinfo):
	releasedata={}
	for rid in find_releases_for_tracks(trackinfo):
		release = lookups.get_release_by_releaseid(rid)
		albumartist=release.artist
		if musicbrainz2.model.Release.TYPE_SOUNDTRACK in release.types:
			directoryname = "Soundtrack"
		else:
			directoryname = albumartist.name
		#print albumartist.name,":",release.title+" ("+rid+".html)"
		releaseevents=release.getReleaseEvents()
		#print "Release dates:"
		#for ev in releaseevents:
		#	print " ",ev.date
		#print "Track:"
		tracks=release.getTracks()
		trackdata=[]
		for tracknum in range(len(tracks)):
			trk=tracks[tracknum]
			(fname,artist,trackname,dur,trackprints,puid) = trackinfo[tracknum+1]
			if trk.artist is None:
				artist=albumartist.name
				sortartist=albumartist.sortName
				artistid=albumartist.id
			else:
				artist=trk.artist.name
				sortartist=trk.artist.sortName
				artistid=trk.artist.id
			#print " ",tracknum+1,"-",artist,"-",trk.title,"%2d:%06.3f" % (int(dur/60000),(dur%6000)/1000),`fname`
			trackdata.append((tracknum+1,artist,sortartist,trk.title,dur,fname,artistid,trk.id))
		asin = lookups.get_asin_from_release(release)
		albuminfo = (
			directoryname,
			release.title,
			rid+".html",
			[x.date for x in releaseevents],
			asin,
			trackdata,
			albumartist,
			release.id,
		)
		yield albuminfo

if __name__=="__main__":
	trackinfo=get_dir_info(sys.argv[1])
	for (albumartist,release,rid,releases,asin,trackdata,albumartistid,releaseid) in guess_album(trackinfo):
		print albumartist,"-",release
		print "ASIN:",asin
		print "Release Dates:",
		for i in releases:
			print "",i
		for (tracknum,artist,sortartist,title,dur,fname,artistid,trkid) in trackdata:
			print "",tracknum,"-",artist,"-",title,"%2d:%06.3f" % (int(dur/60000),(dur % 60000)/1000)
			print " ",fname

