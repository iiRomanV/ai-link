import io
p = 'core/response_validator.py'
s = io.open(p, encoding='utf-8').read()
s = s.replace('if name ==', 'if name ==')
io.open(p, 'w', encoding='utf-8').write(s)
print('done')
