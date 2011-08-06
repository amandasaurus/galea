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

def main(args):
    # get arguments
    parser = OptionParser()
    parser.add_option("-o", dest="output_filename", default="transitions.webm")
    parser.add_option("-l", dest="transition_length", default=0.5)
    parser.add_option("-t", dest="transition_type", default=21)

    options, args = parser.parse_args()
    files = args

    transition_length = long(float(options.transition_length) * gst.SECOND)

    comp, controllers = composition(int(options.transition_type), transition_length, files)
    color= gst.element_factory_make("ffmpegcolorspace")
    enc = gst.element_factory_make("theoraenc")
    mux = gst.element_factory_make("oggmux")
    sink = gst.element_factory_make("filesink")
    sink.props.location = options.output_filename
    pipeline = gst.Pipeline()
    pipeline.add(comp, color, enc, mux, sink)
    color.link(enc)
    enc.link(mux)
    mux.link(sink)

    def on_pad(comp, pad, elements):
        convpad = elements.get_compatible_pad(pad, pad.get_caps())
        pad.link(convpad)
    comp.connect("pad-added", on_pad, color)

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

    TRANSITION_LENGTH = long(0.1 * gst.SECOND)
    assert all(x[1] > transition_length for x in files)

    composition  = gst.element_factory_make("gnlcomposition")
    current_start = 0
    for idx, (fileuri, length) in enumerate(files):
        gsrc = gst.element_factory_make("gnlfilesource")
        gsrc.props.location = fileuri
        gsrc.props.start          = current_start
        gsrc.props.duration       = length
        gsrc.props.media_start    = 0
        gsrc.props.media_duration = length
        gsrc.props.priority       = len(files) - idx + 1
        composition.add(gsrc)
        current_start = current_start + length - transition_length

    controllers = []

    assert len(files) > 0, files
    current_start = files[0][1] - transition_length
    for fileuri, length in files[1:]:
        trans, controller = transition(transition_type, transition_length)
        controllers.append(controller)

        op = gst.element_factory_make("gnloperation")
        op.add(trans)
        op.props.start          = current_start
        op.props.duration       = transition_length
        op.props.media_start    = 0
        op.props.media_duration = transition_length
        op.props.priority       = 1
        composition.add(op)
        current_start = current_start + length - transition_length

    return composition, controllers



def transition(transition_type, length):
    bin = gst.Bin()
    alpha1 = gst.element_factory_make("alpha")
    queue = gst.element_factory_make("queue")
    smpte  = gst.element_factory_make("smptealpha")
    smpte.props.type = abs(transition_type)
    smpte.props.border = 20000
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
