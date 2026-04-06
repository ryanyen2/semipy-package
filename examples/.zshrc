export PS1="\W \$ "
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion
code () { VSCODE_CWD="$PWD" open -n -b "com.microsoft.VSCode" --args $* ;}


# >>> conda initialize >>>
# !! Contents within this block are managed by 'conda init' !!
__conda_setup="$('/Users/r4yen/miniconda3/bin/conda' 'shell.zsh' 'hook' 2> /dev/null)"
if [ $? -eq 0 ]; then
    eval "$__conda_setup"
else
    if [ -f "/Users/r4yen/miniconda3/etc/profile.d/conda.sh" ]; then
        . "/Users/r4yen/miniconda3/etc/profile.d/conda.sh"
    else
        export PATH="/Users/r4yen/miniconda3/bin:$PATH"
    fi
fi
unset __conda_setup
# <<< conda initialize <<<



# JINA_CLI_BEGIN

## autocomplete
if [[ ! -o interactive ]]; then
    return
fi

compctl -K _jina jina

_jina() {
  local words completions
  read -cA words

  if [ "${#words}" -eq 2 ]; then
    completions="$(jina commands)"
  else
    completions="$(jina completions ${words[2,-2]})"
  fi

  reply=(${(ps:
:)completions})
}

# session-wise fix
ulimit -n 4096
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

# JINA_CLI_END









# Load Angular CLI autocompletion.
source <(ng completion script)
export PATH="/opt/homebrew/opt/ruby/bin:/usr/local/lib/ruby/gems/3.1.0/bin:$PATH"
export PATH="$HOME/.dotnet/tools:$PATH"

# Added by Antigravity
export PATH="/Users/r4yen/.antigravity/antigravity/bin:$PATH"

# opencode
export PATH=/Users/r4yen/.opencode/bin:$PATH

# pnpm
export PNPM_HOME="/Users/r4yen/Library/pnpm"
case ":$PATH:" in
  *":$PNPM_HOME:"*) ;;
  *) export PATH="$PNPM_HOME:$PATH" ;;
esac
# pnpm end
export PATH="$HOME/.local/bin:$PATH"
export PATH="$HOME/.local/bin:$PATH"
export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"
