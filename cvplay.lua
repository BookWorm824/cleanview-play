-- CleanView Play's part inside the mpv player (cvplay.py starts mpv with it).
-- For each video it reads the list CleanView Play wrote (the CVPLAY_LIST file): the sound
-- track CleanView listened to, the filters that mute words and lines and turn down music,
-- the black over each cut, and the cuts to jump past. The video file itself is never changed.
local utils = require "mp.utils"

local list = {videos = {}}
do
    local path = os.getenv("CVPLAY_LIST")
    local f = path and io.open(path, "r")
    if f then
        local text = f:read("*a")
        f:close()
        list = utils.parse_json(text) or list
    else
        mp.msg.warn("CleanView Play: no list to play with")
    end
end

local function same(p)
    return (p or ""):gsub("\\", "/"):lower()
end

local current = nil

local function find(path)
    local want = same(path)
    for _, v in ipairs(list.videos or {}) do
        if same(v.path) == want then
            return v
        end
    end
    return nil
end

-- before the video starts: its sound track and its filters (nothing for a video not on the list)
mp.add_hook("on_preloaded", 50, function()
    current = find(mp.get_property("path"))
    if not current then
        mp.set_property("af", "")
        mp.set_property("vf", "")
        return
    end
    if current.audio_index then
        for _, t in ipairs(mp.get_property_native("track-list") or {}) do
            if t.type == "audio" and t["ff-index"] == current.audio_index then
                mp.set_property_number("aid", t.id)
            end
        end
    end
    mp.set_property("af", current.af or "")
    mp.set_property("vf", current.vf or "")
    mp.set_property("force-media-title", current.title or "")
end)

mp.register_event("end-file", function()
    current = nil
end)

-- jump past a cut as soon as it's reached (also when the seek bar is dragged into one);
-- the picture is already black and the sound silent there, so nothing of it shows
local EARLY = 0.04      -- a frame early
local function skip_to(t)
    for _, r in ipairs(current.skips or {}) do
        if t >= r[1] - EARLY and t < r[2] - 0.05 then
            return r[2]
        end
    end
    return nil
end

mp.observe_property("time-pos", "number", function(_, t)
    if not current or not t then
        return
    end
    local to = skip_to(t)
    if to then
        local dur = mp.get_property_number("duration") or current.duration or 0
        if dur > 0 and to >= dur - 0.25 then
            mp.command("playlist-next force")       -- the cut runs to the end
        else
            mp.commandv("seek", tostring(to), "absolute+exact")
        end
    end
end)

-- Inside the CleanView Play window (CVPLAY_REMOTE set): mpv's own keys are all off, and a key
-- or click on the picture is passed to the window, whose big buttons do the same things.
if os.getenv("CVPLAY_REMOTE") then
    local keys = {
        SPACE = "pause", MBTN_LEFT = "pause", ["p"] = "pause", PLAYPAUSE = "pause",
        LEFT = "back", RIGHT = "forward",
        UP = "louder", DOWN = "quieter", WHEEL_UP = "louder", WHEEL_DOWN = "quieter",
        ["f"] = "fullscreen", ["F"] = "fullscreen", MBTN_LEFT_DBL = "fullscreen",
        ESC = "esc", ["q"] = "stop", STOP = "stop", NEXT = "next",
        ["c"] = "subs",
    }
    for key, what in pairs(keys) do
        mp.add_forced_key_binding(key, "cvplay-" .. key, function()
            mp.commandv("script-message", "cvplay-key", what)
        end)
    end
end

-- Clean subtitles (the owner, 2026-11-14: "people can still access subtitles in some way
-- right?"): each video's own .srt from CleanView Play, with the language blanked and muted
-- lines and cut scenes left out. Off unless turned on with the window's Subtitles button
-- ("script-message cvplay-subs"); once on, they stay on for the next video. The video's own
-- subtitle tracks are never shown when anything is muted: they'd show the words that are muted.
local subs_on = false
local clean_sid = nil

mp.register_event("file-loaded", function()
    clean_sid = nil
    if current and current.srt then
        mp.commandv("sub-add", current.srt, "auto", "Subtitles (clean)")
        for _, t in ipairs(mp.get_property_native("track-list") or {}) do
            if t.type == "sub" and t.external then
                clean_sid = t.id            -- the one just added comes last
            end
        end
    elseif current and current.own_subs then
        -- just watching, nothing muted (1.0.9): the video's own subtitles (or a .srt beside it)
        for _, t in ipairs(mp.get_property_native("track-list") or {}) do
            if t.type == "sub" and not clean_sid then
                clean_sid = t.id
            end
        end
    end
    -- the window shows its Subtitles button only when there are some
    mp.set_property_native("user-data/cvplay/subs", clean_sid ~= nil)
    if subs_on and clean_sid then
        mp.set_property_number("sid", clean_sid)
    else
        mp.set_property("sid", "no")
    end
end)

mp.register_script_message("cvplay-subs", function()
    if not clean_sid then
        return
    end
    subs_on = not subs_on
    if subs_on then
        mp.set_property_number("sid", clean_sid)
    else
        mp.set_property("sid", "no")
    end
end)
