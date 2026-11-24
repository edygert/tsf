# tsf picker key for bash.
#
# On a command line, press the key (default Ctrl-X Ctrl-T) to open tsf with
# the name at the cursor as the search; Enter puts the chosen field or
# protocol name on the command line. Install with: tsf --install-shell bash
#
# TSF_PICK_KEY   key sequence in readline syntax, set before this file is
#                loaded (default: \C-x\C-t)
# TSF_FIELDS     JSON file written by tsf --save, used instead of tshark
# TSF_TSHARK     tshark executable (default: tshark on $PATH)

case $- in *i*) ;; *) return 0 ;; esac
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    echo "tsf: the picker key needs bash 4.0 or later; this is bash ${BASH_VERSION:-unknown}" >&2
    return 0
fi

# READLINE_POINT counts characters (checked with bash 5.2 in a UTF-8
# locale), the same unit as ${var:offset:length}.
_tsf_pick() {
    local before=${READLINE_LINE:0:READLINE_POINT}
    local after=${READLINE_LINE:READLINE_POINT}
    local left= right= out
    [[ $before =~ [A-Za-z0-9._-]+$ ]] && left=${BASH_REMATCH[0]}
    [[ $after =~ ^[A-Za-z0-9._-]+ ]] && right=${BASH_REMATCH[0]}
    out=$(command tsf --pick --query="$left$right") || return 0
    before=${before:0:${#before}-${#left}}
    after=${after:${#right}}
    READLINE_LINE=$before$out$after
    READLINE_POINT=$((${#before} + ${#out}))
}

bind -m emacs -x "\"${TSF_PICK_KEY:-\\C-x\\C-t}\": _tsf_pick"
bind -m vi-insert -x "\"${TSF_PICK_KEY:-\\C-x\\C-t}\": _tsf_pick"
