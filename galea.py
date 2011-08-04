#! /usr/bin/python

from __future__ import division

import sys, os.path
import gobject
import pygst
pygst.require("0.10")
import gst

controllers = []

def duration(filepath):
    """Given a filepath, return the length (in nanoseconds) of the media"""
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
    controllers, comp = composition(args)
    color= gst.element_factory_make("ffmpegcolorspace")
    enc = gst.element_factory_make("theoraenc")
    mux = gst.element_factory_make("oggmux")
    sink = gst.element_factory_make("filesink")
    sink.props.location = "./transitions.ogv"
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
    print "done"

def composition(files):
    assert len(files) > 0
    files = [('file://'+os.path.abspath(x), duration(x)) for x in files]

    TRANSITION_LENGTH = long(2 * gst.SECOND)
    TRANSITION_TYPE = 21
    assert all(x[1] > TRANSITION_LENGTH for x in files)

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
        print "added file %s with priority %d, start %r, duration %r, end %r" % (gsrc.props.location, gsrc.props.priority, gsrc.props.start/gst.SECOND, gsrc.props.duration/gst.SECOND, (gsrc.props.start + gsrc.props.duration)/gst.SECOND)
        current_start = current_start + length - TRANSITION_LENGTH

    global controllers

    assert len(files) > 0, files
    current_start = files[0][1] - TRANSITION_LENGTH
    for fileuri, length in files[1:]:
        trans, controller = transition(TRANSITION_TYPE, TRANSITION_LENGTH)
        controllers.append(controller)

        op = gst.element_factory_make("gnloperation")
        op.add(trans)
        op.props.start          = current_start
        op.props.duration       = TRANSITION_LENGTH
        op.props.media_start    = 0
        op.props.media_duration = TRANSITION_LENGTH
        op.props.priority       = 1
        composition.add(op)
        print "added op with priority %d start %r" % (op.props.priority, op.props.start/gst.SECOND)
        current_start = current_start + length - TRANSITION_LENGTH

    return controllers, composition



def transition(transition_type, length):
    bin = gst.Bin()
    alpha1 = gst.element_factory_make("alpha")
    queue = gst.element_factory_make("queue")
    smpte  = gst.element_factory_make("smptealpha")
    smpte.props.type = transition_type
    mixer  = gst.element_factory_make("videomixer")

    bin.add(alpha1, queue, smpte, mixer)
    alpha1.link(mixer)
    queue.link(smpte)
    smpte.link(mixer)

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
