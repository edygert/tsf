# tsf picker key and Tab for zsh.
#
# On a command line, press the key (default Ctrl-X Ctrl-T) to open tsf with
# the name at the cursor as the search; Enter puts the chosen field or
# protocol name on the command line. Install with: tsf --install-shell zsh
#
# Tab opens the same picker in the arguments of tshark's -Y, -R, -e, -j, -J
# and -O options, and does what it did before everywhere else.
#
# Set these before this file is loaded:
# TSF_PICK_KEY   key sequence in bindkey syntax (default: ^X^T)
# TSF_TAB        0 leaves Tab unchanged (default: 1)
# TSF_FIELDS     JSON file written by tsf --save, used instead of tshark
# TSF_TSHARK     tshark executable (default: tshark on $PATH)

[[ -o interactive ]] || return 0

_tsf_pick() {
    emulate -L zsh
    setopt extended_glob
    local left=${(M)LBUFFER%%[A-Za-z0-9._-]#}
    local right=${(M)RBUFFER##[A-Za-z0-9._-]#}
    local out
    if out=$(command tsf --pick --query="$left$right" </dev/tty); then
        LBUFFER=${LBUFFER[1,$#LBUFFER-$#left]}$out
        RBUFFER=${RBUFFER[$#right+1,-1]}
    fi
    zle reset-prompt
}

zle -N _tsf_pick
bindkey -M emacs "${TSF_PICK_KEY:-^X^T}" _tsf_pick
bindkey -M viins "${TSF_PICK_KEY:-^X^T}" _tsf_pick

[[ ${TSF_TAB:-1} == 0 ]] && return 0

# Status 0 if the cursor is in an argument of tshark -Y, -R, -e, -j, -J or -O.
_tsf_tab_wants_picker() {
    emulate -L zsh
    setopt extended_glob
    (( $+commands[tsf] )) || return 1
    local -a words
    words=(${(z)LBUFFER})
    # ${(z)} drops a trailing empty word: an unquoted blank before the cursor
    # means the cursor starts a new, empty word.
    [[ $LBUFFER == *[[:blank:]] && ${words[-1]} != *[[:blank:]] ]] && words+=("")
    (( $#words > 1 )) || return 1
    local cur=${words[-1]} prev=${words[-2]} i start=1 cmd=
    # The command: the first word after the last separator, skipping NAME=value
    # assignments and precommands.
    for (( i = $#words - 1; i >= 1; i-- )); do
        case ${words[i]} in
            ('|'|'||'|'&&'|';'|'&'|'|&'|'('|'{'|then|do|else) start=$((i + 1)); break ;;
        esac
    done
    for (( i = start; i < $#words; i++ )); do
        case ${words[i]} in
            ([A-Za-z_][A-Za-z0-9_]#=*|sudo|command|exec|env|nice|nohup|time|noglob|builtin) ;;
            (*) cmd=${words[i]}; break ;;
        esac
    done
    [[ ${cmd:t} == tshark ]] && (( i < $#words )) || return 1
    # Just after a closing quote, e.g. -Y 'dns.a'|: nothing to fill in.
    local q=${cur[-1]}
    if [[ $q == [\'\"] ]] && (( ${#${cur//[^$q]/}} % 2 == 0 )); then
        return 1
    fi
    [[ $cur == -[YRejJO]?* ]] && return 0    # option with its argument attached
    [[ $prev == -[YRejJO] ]] && return 0     # option, then its argument
    return 1
}

# $1: keymap whose previous Tab widget is the fallback.
_tsf_tab() {
    if _tsf_tab_wants_picker; then
        zle _tsf_pick
    else
        zle ${_tsf_tab_next[$1]:-expand-or-complete}
    fi
}
# One widget per keymap. The keymap is passed as an argument, not read from
# $WIDGET: in a widget called by another widget (e.g. fzf-completion, which
# calls the Tab widget it found when it was loaded), $WIDGET is the name of
# the outer widget.
_tsf_tab_emacs() { _tsf_tab emacs }
_tsf_tab_viins() { _tsf_tab viins }

typeset -gA _tsf_tab_next
() {
    local keymap next
    for keymap in emacs viins; do
        next=${${(z)$(bindkey -M $keymap '^I')}[2]}
        [[ -z $next || $next == undefined-key || $next == _tsf_tab_* ]] && next=expand-or-complete
        _tsf_tab_next[$keymap]=$next
        zle -N _tsf_tab_$keymap
        bindkey -M $keymap '^I' _tsf_tab_$keymap
    done
}
