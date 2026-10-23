# Records assets/demo.gif. Run from the repository root after `uv sync`:
#   vhs docs/demo.tape
Output assets/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1400
Set Height 360
Set Padding 16
Set TypingSpeed 40ms
Set Theme "Catppuccin Mocha"

Hide
Type "export PATH=$PWD/.venv/bin:$PATH PS1='$ ' && D=$(mktemp -d) && cd $D" Enter
Type "printf 'Менеджер: Назовите ваш телефон.\nКлиент: Меня зовут Иван Петров, восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять.\nКлиент: Почта иван точка петров собака мэйл точка ру.\n' > demo.txt" Enter
Type "printf 'Менеджер: Код 123 45 67 сохраните.\n' > demo2.txt" Enter
Type "clear" Enter
Show

Type "cat demo.txt" Enter
Sleep 2s
Type "scrub demo.txt -o demo.clean.txt" Enter
Sleep 5s
Type "cat demo.clean.txt" Enter
Sleep 4s
Type "clear" Enter
Type "scrub demo2.txt -o demo2.clean.txt && cat demo2.clean.txt" Enter
Sleep 4s
