# tsf picker key for zsh.
#
# On a command line, press the key (default Ctrl-X Ctrl-T) to open tsf with
# the name at the cursor as the search; Enter puts the chosen field or
# protocol name on the command line. Install with: tsf --install-shell zsh
#
# TSF_PICK_KEY   key sequence in bindkey syntax, set before this file is
#                loaded (default: ^X^T)
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
