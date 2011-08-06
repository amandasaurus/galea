#! /usr/bin/python

from __future__ import division

import sys, os.path
import gobject
import pygst
pygst.require("0.10")
import gst
from optparse import OptionParser

def duration(filepath):
    """Given a filepath, return the length (in nanoseconds) of the media"""
    assert os.path.isfile(filepath), "File %s doesn't exist" % filepath
    gobject.threads_init()
    d = gst.parse_launch("filesrc name=source ! decodebin2 ! fakesink")
    source = d.get_by_name("source")
    source.set_property("location", filepath)
    d.set_state(gst.STATE_PLAYING)
    d.get_state()
    format = gst.Format(gst.FORMAT_TIME)
    duration = d.query_duration(format)[0]
    d.set_state(gst.STATE_NULL)
    return duration

def music_stream(music_filename, all_video_files, transition_length):
    offset = 0
    if ',' in music_filename and not os.path.isfile(music_filename):
        music_filename, offset = music_filename.split(",", 1)
        offset = float(offset)
        assert offset >= 0
        offset = long(offset * gst.SECOND)
        
    assert os.path.isfile(music_filename)
    file_lengths = sum(duration(x) for x in all_video_files) - transition_length * (len(all_video_files) - 1)
    music_src = gst.element_factory_make("gnlfilesource")
    music_src.props.location = "file://"+os.path.abspath(music_filename)
    music_src.props.start          = 0
    music_src.props.duration       = file_lengths
    music_src.props.media_start    = offset
    music_src.props.media_duration = file_lengths
    music_src.props.priority       = 1
    acomp = gst.element_factory_make("gnlcomposition")
    acomp.add(music_src)
    return acomp


def main(args):
    # get arguments
    parser = OptionParser()
    parser.add_option("-o", '--output', dest="output_filename", default="video")
    parser.add_option("-l", '--transition-length', dest="transition_length", default=0.5)
    parser.add_option("-t", '--transition-type', dest="transition_type", default=-21)
    parser.add_option("-m", '--music', dest="music", default=None)
    parser.add_option("-f", "--format", dest="format", default="ogv", help="Type of video format output")

    formats = {
        'ogv':  { 'venc': 'theoraenc', 'aenc': 'vorbisenc', 'muxer': 'oggmux'},
        'webm': { 'venc': 'vp8enc', 'aenc': 'vorbisenc', 'muxer': 'webmmux' },
        'mp4':  { 'venc': 'x264enc', 'aenc': 'lame', 'muxer': 'mp4mux' },
    }

    options, args = parser.parse_args()
    video_files = args

    assert options.format in formats, "Unknown format %r, known formats: %r" % (options.format, formats.keys())
    format = formats[options.format]

    transition_length = long(float(options.transition_length) * gst.SECOND)

    vcomp, controllers = composition(int(options.transition_type), transition_length, video_files)

    if options.music:
        acomp = music_stream(options.music, video_files, transition_length)

    vqueue = gst.element_factory_make("queue")
    color= gst.element_factory_make("ffmpegcolorspace")
    venc = gst.element_factory_make(format['venc'])
    mux = gst.element_factory_make(format['muxer'])
    progress = gst.element_factory_make("progressreport")
    sink = gst.element_factory_make("filesink")
    sink.props.location = options.output_filename + "." + options.format
    pipeline = gst.Pipeline()
    pipeline.add(vcomp, vqueue, color, venc, mux, progress, sink)
    vqueue.link(color)
    color.link(venc)
    venc.link(mux)
    mux.link(progress)
    progress.link(sink)

    if options.music:
        audioconvert = gst.element_factory_make("audioconvert")
        aenc = gst.element_factory_make(format['aenc'])
        queue = gst.element_factory_make("queue")
        muxqueue = gst.element_factory_make("queue")
        pipeline.add(audioconvert, aenc, queue)
        pipeline.add(muxqueue)
        pipeline.add(acomp)
        queue.link(audioconvert)
        audioconvert.link(aenc)
        aenc.link(muxqueue)
        muxqueue.link(mux)

    def on_pad(comp, pad, elements):
        convpad = elements.get_compatible_pad(pad, pad.get_caps())
        pad.link(convpad)
    vcomp.connect("pad-added", on_pad, vqueue)
    if options.music:
        acomp.connect("pad-added", on_pad, queue)

    loop = gobject.MainLoop(is_running=True)
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    def on_message(bus, message, loop):
        if message.type == gst.MESSAGE_EOS:
            loop.quit()
        elif message.type == gst.MESSAGE_ERROR:
            print message
            loop.quit()
    bus.connect("message", on_message, loop)
    pipeline.set_state(gst.STATE_PLAYING)
    loop.run()
    pipeline.set_state(gst.STATE_NULL)

def composition(transition_type, transition_length, files):
    assert len(files) > 0
    files = [('file://'+os.path.abspath(x), duration(x)) for x in files]

    # If there is a video that's shorter than twice transition_length, there
    # won't be enough time for the transition to go in adn then out. I don't
    # know if you can have overlapping transitions, so prevent this from
    # happening in the first place
    assert all(x[1] > transition_length*2 for x in files)

    composition  = gst.element_factory_make("gnlcomposition")

    ## Add a gnlfilesource for each of the videos

    # The position in the main timeline (in nanoseconds) that the next video
    # should start playing at. Initally at 0 cause the first video plays at the
    # start
    current_start = 0
    for idx, (fileuri, length) in enumerate(files):
        gsrc = gst.element_factory_make("gnlfilesource")
        gsrc.props.location       = fileuri
        gsrc.props.start          = current_start
        gsrc.props.duration       = length
        gsrc.props.media_start    = 0
        gsrc.props.media_duration = length

        # The earlier the video, the higher the priority number (which means a
        # less priority video). i.e. video #1 should have a higher priority
        # number than video #2, to ensure that video #2 will play instead of
        # video #1
        # The priority of gnlfilesources is a bit of yak shaving mystery and is
        # something that you have to poke with and do black magic to make it
        # work
        gsrc.props.priority       = len(files) - idx + 1

        composition.add(gsrc)
        current_start = current_start + length - transition_length

    controllers = []

    ## Make a transition for each video (bar the first)
    assert len(files) > 0, files

    # The time (in nanoseconds) that the transitions should start at.
    # The first transition should start transition_length nanoseconds before the first video ends
    transition_start = files[0][1] - transition_length
    for fileuri, length in files[1:]:
        trans, controller = transition(transition_type, transition_length)

        # we need to keep references to the controllers around, lest gstreamer break
        # cf. http://notes.brooks.nu/2011/01/python-gstreamer-controller/
        controllers.append(controller)

        op = gst.element_factory_make("gnloperation")
        op.add(trans)
        op.props.start          = transition_start
        op.props.duration       = transition_length
        op.props.media_start    = 0
        op.props.media_duration = transition_length
        op.props.priority       = 1
        composition.add(op)
        transition_start = transition_start + length - transition_length


    # return a reference to the controllers aswell
    return composition, controllers



def transition(transition_type, length):
    """
    Given a smpte transition type number & length in nanoseconds, returns a gstreamer bin that does that transition
    """
    # Rather than use the smpte transition itself, this uses the smptealpha
    # approach. cf. http://notes.brooks.nu/gstreamer-video-crossfade-example/
    bin = gst.Bin()
    alpha1 = gst.element_factory_make("alpha")
    queue = gst.element_factory_make("queue")
    smpte  = gst.element_factory_make("smptealpha")

    smpte.props.type = abs(transition_type)

    # A fuzzy transition, rather than a sharp edge
    smpte.props.border = 20000

    # Invert the transition if it's a negative number
    smpte.props.invert = transition_type < 0

    mixer  = gst.element_factory_make("videomixer")

    bin.add(alpha1, queue, smpte, mixer)

    alpha1.link(mixer)
    queue.link(smpte)
    smpte.link(mixer)

    # we need to keep the controller (and all controllers made) around till the
    # end otherwise they get cleared up and deleted and things break
    controller = gst.Controller(smpte, "position")
    controller.set_interpolation_mode("position", gst.INTERPOLATE_LINEAR)
    controller.set("position", 0, 1.0)
    controller.set("position", length, 0.0)

    bin.add_pad(gst.GhostPad("sink1", alpha1.get_pad("sink")))
    bin.add_pad(gst.GhostPad("sink2", queue.get_pad("sink")))
    bin.add_pad(gst.GhostPad("src",   mixer.get_pad("src")))

    return bin, controller


if __name__ == '__main__':
    main(sys.argv[1:])
