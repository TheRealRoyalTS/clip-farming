from clipfarm.chat import parse_line


def test_plain_privmsg():
    m = parse_line(":alice!alice@alice.tmi.twitch.tv PRIVMSG #SomeChan :hello world", 5.0)
    assert (m.channel, m.user, m.text, m.ts) == ("somechan", "alice", "hello world", 5.0)


def test_tagged_privmsg_strips_tags():
    line = "@badges=;color=#fff :bob!bob@bob.tmi.twitch.tv PRIVMSG #c :KEKW KEKW"
    assert parse_line(line, 1.0).text == "KEKW KEKW"


def test_message_containing_colon_and_privmsg_word():
    m = parse_line(":a!a@a.tmi.twitch.tv PRIVMSG #c :look: PRIVMSG #x :trick", 1.0)
    assert m.channel == "c" and m.text == "look: PRIVMSG #x :trick"


def test_non_chat_lines_are_ignored():
    assert parse_line(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!", 1.0) is None
    assert parse_line("PING :tmi.twitch.tv", 1.0) is None
    assert parse_line(":x JOIN #c", 1.0) is None
