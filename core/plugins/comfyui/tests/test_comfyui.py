"""The ComfyUI plugin against a ComfyUI held in memory. No test reaches a network, the real
workflow folder or the chat's image store: `comfy` points the plugin's two folders at tmp_path
and `server` stands in for the HTTP client."""
import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _sapphire_root(start: Path) -> Path:
    root = start.absolute()
    while not (root / 'sapphire.py').is_file():
        if root.parent == root:
            raise RuntimeError(f'Sapphire root (sapphire.py) not found above {start}')
        root = root.parent
    return root


_ROOT = str(_sapphire_root(Path(__file__).parent))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plugins.comfyui import comfy as cf  # noqa: E402


def node(cls, title=None, **inputs):
    n = {'class_type': cls, 'inputs': inputs}
    if title:
        n['_meta'] = {'title': title}
    return n


def sd_graph():
    """A plain text-to-image graph whose NEGATIVE encoder has the lower id."""
    return {
        '3': node('KSampler', model=['4', 0], positive=['7', 0], negative=['6', 0], latent_image=['5', 0], seed=1, steps=20),
        '4': node('CheckpointLoaderSimple', ckpt_name='sdxl.safetensors'),
        '5': node('EmptyLatentImage', width=1024, height=1024, batch_size=1),
        '6': node('CLIPTextEncode', 'Negative', text='blurry', clip=['4', 1]),
        '7': node('CLIPTextEncode', 'Positive', text='old prompt', clip=['4', 1]),
        '8': node('VAEDecode', samples=['3', 0], vae=['4', 2]),
        '9': node('SaveImage', images=['8', 0], filename_prefix='x'),
    }


def flux_graph():
    return {
        '1': node('CLIPTextEncode', text='old', clip=['9', 0]),
        '2': node('FluxGuidance', conditioning=['1', 0], guidance=3.5),
        '3': node('BasicGuider', model=['9', 0], conditioning=['2', 0]),
        '4': node('RandomNoise', noise_seed=7),
        '5': node('SamplerCustomAdvanced', noise=['4', 0], guider=['3', 0], sampler=['9', 0], sigmas=['9', 0], latent_image=['6', 0]),
        '6': node('EmptySD3LatentImage', width=1024, height=1024, batch_size=1),
        '8': node('SaveImage', images=['5', 0]),
    }


def video_graph():
    """Image to video: conditioning passes through two nodes, the size is typed on the i2v node."""
    return {
        'img': node('LoadImage', image='start.png'),
        'pos': node('CLIPTextEncode', text='old', clip=['c', 0]),
        'neg': node('CLIPTextEncode', text='ugly', clip=['c', 0]),
        'cond': node('LTXVConditioning', positive=['pos', 0], negative=['neg', 0], frame_rate=24),
        'i2v': node('LTXVImgToVideo', positive=['cond', 0], negative=['cond', 1], vae=['v', 0], image=['img', 0],
                    width=768, height=512, length=121),
        'guide': node('CFGGuider', model=['u', 0], positive=['i2v', 0], negative=['i2v', 1], cfg=1.0),
        'noise': node('RandomNoise', noise_seed=1),
        'samp': node('SamplerCustomAdvanced', noise=['noise', 0], guider=['guide', 0], latent_image=['i2v', 2]),
        'dec': node('VAEDecode', samples=['samp', 0], vae=['v', 0]),
        'save': node('SaveVideo', video=['dec', 0], filename_prefix='clip'),
    }


def hires_graph():
    """Two passes with a latent upscale between them, and a leftover branch wired to nothing."""
    g = sd_graph()
    g.update({
        '10': node('LatentUpscale', samples=['3', 0], width=2048, height=2048, upscale_method='nearest-exact'),
        '11': node('KSampler', model=['4', 0], positive=['7', 0], negative=['6', 0], latent_image=['10', 0], seed=2),
        '8': node('VAEDecode', samples=['11', 0], vae=['4', 2]),
        # leftover: exported with the rest, runs never
        '20': node('CLIPTextEncode', 'Old idea', text='unused', clip=['4', 1]),
        '21': node('EmptyLatentImage', width=512, height=512, batch_size=1),
        '22': node('KSampler', model=['4', 0], positive=['20', 0], negative=['6', 0], latent_image=['21', 0], seed=9),
        '23': node('VAEDecode', samples=['22', 0], vae=['4', 2]),
    })
    return g


def png(mode='RGB', color=(200, 30, 30), size=(64, 48), text=None):
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    for k, v in (text or {}).items():
        info.add_text(k, v)
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, 'PNG', pnginfo=info)
    return buf.getvalue()


class Reply:
    def __init__(self, status=200, body=None, content=b''):
        self.status_code, self._body, self.content = status, body, content

    def json(self):
        if self._body is None:
            raise ValueError('not json')
        return self._body

    def iter_content(self, _n):
        if self.content:
            yield self.content


class Server:
    """ComfyUI's doors. A queued graph finishes at once with `outputs`; `refuse` is a list of
    400 bodies handed out first; `running`/`pending` hold prompt ids still in the queue."""

    def __init__(self):
        self.calls, self.graphs, self.uploads = [], [], []
        self.refuse, self.partial, self.history, self.files = [], [], {}, {}
        self.running, self.pending, self.finish = [], [], True
        self.outputs = {'9': {'images': [{'filename': 'out.png', 'subfolder': '', 'type': 'output'}]}}
        self.files['out.png'] = png()
        self.info = {}
        self.down = None

    def _path(self, url):
        return '/' + url.split('://', 1)[1].split('/', 1)[1]

    def get(self, url, **kw):
        path = self._path(url)
        self.calls.append(('GET', path))
        if self.down:
            raise self.down
        if path == '/system_stats':
            return Reply(body={'system': {'comfyui_version': '0.46.0', 'os': 'linux'},
                               'devices': [{'name': 'cuda:0 NVIDIA Test : cudaMallocAsync', 'vram_total': 32 * 2 ** 30}]})
        if path == '/object_info':
            return Reply(body=self.info)
        if path.startswith('/object_info/'):
            cls = path.rsplit('/', 1)[1]
            return Reply(body={cls: self.info[cls]}) if cls in self.info else Reply(404)
        if path == '/queue':
            item = lambda p: [0, p, {}, {}, []]
            return Reply(body={'queue_running': [item(p) for p in self.running], 'queue_pending': [item(p) for p in self.pending]})
        if path == '/history':
            return Reply(body=self.history)
        if path.startswith('/history/'):
            pid = path.rsplit('/', 1)[1]
            return Reply(body={pid: self.history[pid]} if pid in self.history else {})
        if path == '/view':
            name = kw['params']['filename']
            return Reply(content=self.files[name]) if name in self.files else Reply(404)
        return Reply(404)

    def post(self, url, **kw):
        path = self._path(url)
        self.calls.append(('POST', path, kw.get('json')))
        if self.down:
            raise self.down
        if path == '/prompt':
            self.graphs.append(json.loads(json.dumps(kw['json']['prompt'])))
            if self.refuse:
                return Reply(400, self.refuse.pop(0))
            pid = f'job{len(self.graphs)}'
            if self.partial:
                return Reply(body={'prompt_id': pid, 'number': 1, 'node_errors': self.partial.pop(0)})
            if self.finish:
                self.history[pid] = {'prompt': [len(self.graphs), pid, kw['json']['prompt']],
                                     'status': {'status_str': 'success', 'completed': True, 'messages': []},
                                     'outputs': self.outputs}
            return Reply(body={'prompt_id': pid})
        if path == '/upload/image':
            self.uploads.append(kw['files']['image'][0])
            return Reply(body={'name': kw['files']['image'][0], 'subfolder': ''})
        return Reply(body={})


@pytest.fixture
def comfy(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, 'WORKFLOW_DIR', tmp_path / 'workflows')
    monkeypatch.setattr(cf, 'OUTPUT_DIR', tmp_path / 'outputs')
    return cf


@pytest.fixture
def server(comfy, monkeypatch):
    s = Server()
    real = cf.Comfy.__init__
    monkeypatch.setattr(cf.Comfy, '__init__', lambda self, url=None, http=None: real(self, url, http or s))
    clock = [0.0]                                   # the plugin's own clock, never the process's
    monkeypatch.setattr(cf, '_now', lambda: clock[0])
    monkeypatch.setattr(cf, '_sleep', lambda n: clock.__setitem__(0, clock[0] + max(n, 0.01)))
    return s


@pytest.fixture
def tool(server):
    import plugins.comfyui.tools.comfy_tools as t
    return t


def run(tool, **args):
    return tool.execute('comfy_generate', args, None, {'comfy_url': 'http://127.0.0.1:8188', **args.pop('_settings', {})})


# -- where the prompt, the seed and the size go --------------------------------------------

def test_prompt_goes_to_the_positive_encoder_not_the_first_one():
    g, _ = cf.apply(sd_graph(), {}, 'a cat', 5)
    assert g['7']['inputs']['text'] == 'a cat'
    assert g['6']['inputs']['text'] == 'blurry'
    assert g['3']['inputs']['seed'] == 5


def test_flux_style_graph_is_traced_through_the_guider_and_seeded():
    g, notes = cf.apply(flux_graph(), {}, 'a dog', 99)
    assert g['1']['inputs']['text'] == 'a dog'
    assert g['4']['inputs']['noise_seed'] == 99
    assert notes == []


def test_a_node_that_passes_both_sides_along_keeps_them_apart():
    g, _ = cf.apply(video_graph(), {}, 'she waves', 3)
    assert g['pos']['inputs']['text'] == 'she waves'
    assert g['neg']['inputs']['text'] == 'ugly'


def test_an_encoder_with_its_own_negative_field_keeps_it():
    graph = {'1': node('TextEncodeQwenImageEdit', prompt='old', negative_prompt='bad', clip=['2', 0]),
             '3': node('KSampler', positive=['1', 0], negative=['1', 1], seed=1),
             '4': node('SaveImage', images=['3', 0])}
    g, _ = cf.apply(graph, {}, 'new', 1)
    assert g['1']['inputs'] == {'prompt': 'new', 'negative_prompt': 'bad', 'clip': ['2', 0]}


def test_text_wired_from_a_primitive_is_written_into_the_primitive():
    graph = {'1': node('PrimitiveString', value='old'), '2': node('CLIPTextEncode', text=['1', 0], clip=['9', 0]),
             '3': node('KSampler', positive=['2', 0], seed=1), '4': node('SaveImage', images=['3', 0])}
    g, _ = cf.apply(graph, {}, 'new', 1)
    assert g['1']['inputs']['value'] == 'new' and g['2']['inputs']['text'] == ['1', 0]


def test_a_picked_prompt_node_wins():
    g, _ = cf.apply(sd_graph(), {'prompt_node': '6'}, 'picked', 1)
    assert g['6']['inputs']['text'] == 'picked' and g['7']['inputs']['text'] == 'old prompt'


def test_no_place_for_the_prompt_is_said_not_guessed():
    graph = {'1': node('LoadImage', image='a.png'), '2': node('ImageScale', image=['1', 0]), '3': node('SaveImage', images=['2', 0])}
    with pytest.raises(cf.ComfyError, match='prompt'):
        cf.apply(graph, {}, 'x', 1)
    g, notes = cf.apply(graph, {}, 'x', 1, image='mine.png')
    assert g['1']['inputs']['image'] == 'mine.png' and any('no prompt input' in n for n in notes)


def test_a_shape_keeps_the_pixel_count_and_the_three_words_follow_the_workflow():
    size = lambda g, n: (g[n]['inputs']['width'], g[n]['inputs']['height'])
    g = sd_graph()
    assert cf.reshape(g, 'landscape') and size(g, '5') == (1248, 832)          # a square still: 3:2
    assert cf.reshape(g, 'portrait') and size(g, '5') == (832, 1248)           # the workflow's own ratio, turned
    assert cf.reshape(g, 'square') and size(g, '5') == (1024, 1024)
    assert cf.reshape(g, 9 / 16) and size(g, '5') == (768, 1376)               # her picture's own ratio
    clip = video_graph()
    assert cf.reshape(clip, 'landscape') and size(clip, 'i2v') == (768, 512)   # already lying that way: untouched
    assert cf.reshape(clip, 'portrait') and size(clip, 'i2v') == (512, 768)
    wide = video_graph()
    wide['i2v']['inputs'].update(width=640, height=640)
    assert cf.reshape(wide, 'landscape') and size(wide, 'i2v') == (864, 480)   # a square video: 16:9
    two = sd_graph()
    two['12'] = node('EmptyLatentImage', width=512, height=512, batch_size=1)
    two['13'] = node('KSampler', positive=['7', 0], negative=['6', 0], latent_image=['12', 0], seed=3)
    two['14'] = node('SaveImage', images=['13', 0])
    assert cf.reshape(two, 'landscape') and size(two, '5') == (1248, 832) and size(two, '12') == (640, 416)
    odd = sd_graph()
    odd['5']['inputs'].update(width=1000, height=1000)                         # not on the 32 grid: 16 it is
    assert cf.reshape(odd, 'landscape') and size(odd, '5') == (1232, 816)


def test_a_graph_without_a_seed_says_so():
    graph = sd_graph()
    del graph['3']['inputs']['seed']
    assert any('no seed input' in n for n in cf.apply(graph, {}, 'x', 1)[1])


def test_describe_reads_kind_shape_and_image_input():
    assert cf.describe(sd_graph())['kind'] == 'image' and cf.describe(sd_graph())['shape'] == 'square'
    d = cf.describe(video_graph())
    assert (d['kind'], d['takes_image'], d['shape'], d['prompt_node'], d['image_node']) == ('video', True, 'landscape', 'pos', 'img')
    assert cf.describe(_img2img({}))['shape'] == ''                             # no size anywhere: nothing to set


# -- workflow files ------------------------------------------------------------------------

def test_a_name_is_looked_up_never_joined_into_a_path(comfy, tmp_path):
    cf.write('mine', sd_graph())
    (tmp_path / 'secret.json').write_text(json.dumps(sd_graph()))
    assert cf.find('mine')[0] == 'mine' and cf.find('MINE.json')[0] == 'mine'
    for bad in ('../secret', str(tmp_path / 'secret'), str(tmp_path / 'secret.json'), '', None):
        assert cf.find(bad) == (None, None)
    with pytest.raises(cf.ComfyError, match="No workflow called"):
        cf.pick('../secret')


def test_meta_travels_in_the_file_and_never_reaches_the_graph(comfy):
    cf.write('mine', sd_graph(), {'description': 'portraits', 'junk': 'x'})
    graph, meta = cf.read(cf.find('mine')[1])
    assert meta == {'description': 'portraits'} and cf.META not in graph and set(graph) == set(sd_graph())


def test_pick_uses_the_default_then_the_first(comfy):
    with pytest.raises(cf.ComfyError, match='No ComfyUI workflows'):
        cf.pick(None)
    cf.write('b', sd_graph())
    cf.write('a', sd_graph())
    assert cf.pick(None, 'b')[0] == 'b' and cf.pick(None, 'gone')[0] == 'a' and cf.pick('', '')[0] == 'a'


def test_the_editor_format_is_named_for_what_it_is():
    with pytest.raises(cf.ComfyError, match='editor format'):
        cf.parse({'nodes': [{'id': 1, 'type': 'KSampler'}], 'links': [], 'last_node_id': 1})
    assert set(cf.parse({'prompt': sd_graph(), 'client_id': 'x'})[0]) == set(sd_graph())
    with pytest.raises(cf.ComfyError):
        cf.parse([1, 2])


def test_a_png_comfy_saved_carries_its_workflow():
    graph, _ = cf.from_upload(png(text={'prompt': json.dumps(sd_graph()), 'workflow': '{"nodes": []}'}))
    assert graph['7']['inputs']['text'] == 'old prompt'
    with pytest.raises(cf.ComfyError, match='carries no workflow'):
        cf.from_upload(png())
    with pytest.raises(cf.ComfyError, match="isn't JSON"):
        cf.from_upload(b'not json at all')


def test_slug_is_a_safe_file_name():
    assert cf.slug('My Flow (v2).json') == 'My-Flow-v2-json' and cf.slug('../../x') == 'x'
    assert cf.slug('') == 'workflow' and cf.slug('CON') == 'workflow-CON'


# -- another machine -----------------------------------------------------------------------

def test_the_same_file_on_another_machine_is_matched():
    opts = ['flux/dev.safetensors', 'sdxl.safetensors', 'a/dup.safetensors', 'b/dup.safetensors']
    assert cf.match('flux\\dev.safetensors', opts) == 'flux/dev.safetensors'
    assert cf.match('models/SDXL.safetensors', opts) == 'sdxl.safetensors'
    assert cf.match('dup.safetensors', opts) is None and cf.match('nope.safetensors', opts) is None
    # one name in two DIFFERENT folders is two models (every repo has a model.safetensors)
    assert cf.match('wan/model.safetensors', ['flux/model.safetensors']) is None
    assert "may be another model" in cf._missing('UNETLoader', 'unet_name', 'wan/model.safetensors', ['flux/model.safetensors'])


def test_check_names_what_that_comfy_lacks():
    info = {'KSampler': {'input': {'required': {'seed': ['INT', {}]}}},
            'CheckpointLoaderSimple': {'input': {'required': {'ckpt_name': [['sub/sdxl.safetensors'], {}]}}},
            'EmptyLatentImage': {}, 'CLIPTextEncode': {}, 'VAEDecode': {}}
    found = cf.check(sd_graph(), info)
    assert found['missing_nodes'] == ['SaveImage'] and not found['ok']
    assert found['fixes'][0]['to'] == 'sub/sdxl.safetensors' and not found['missing']
    info['SaveImage'] = {}
    info['CheckpointLoaderSimple']['input']['required']['ckpt_name'] = ['COMBO', {'options': ['other.safetensors']}]
    found = cf.check(sd_graph(), info)
    assert 'models/checkpoints' in found['missing'][0]['words'] and not found['ok']


def _refusal(value_options):
    return {'error': {'type': 'prompt_outputs_failed_validation', 'message': 'Prompt outputs failed validation'},
            'node_errors': {'4': {'class_type': 'CheckpointLoaderSimple', 'errors': [
                {'type': 'value_not_in_list', 'message': 'Value not in list', 'details': 'x' * 5000,
                 'extra_info': {'input_name': 'ckpt_name', 'received_value': 'sdxl.safetensors',
                                'input_config': [value_options, {}]}}]}}}


def test_a_refused_model_path_is_fixed_and_sent_again(server):
    server.refuse = [_refusal(['xl\\sdxl.safetensors'])]
    outputs, fixed = cf.Comfy().run(cf.apply(sd_graph(), {}, 'x', 1)[0], 30)
    assert fixed[0]['to'] == 'xl\\sdxl.safetensors' and outputs
    assert server.graphs[1]['4']['inputs']['ckpt_name'] == 'xl\\sdxl.safetensors'


def test_a_missing_model_is_named_in_words_not_json(server):
    server.refuse = [_refusal(['other.safetensors'])]
    with pytest.raises(cf.ComfyError) as e:
        cf.Comfy().run(sd_graph(), 30)
    assert "'sdxl.safetensors' isn't there" in str(e.value) and 'models/checkpoints' in str(e.value)
    assert len(str(e.value)) < 300 and len(server.graphs) == 1


def test_a_missing_custom_node_is_named(server):
    server.refuse = [{'error': {'type': 'missing_node_type', 'message': 'x', 'extra_info': {'class_type': 'FancyNode'}},
                      'node_errors': {}}]
    with pytest.raises(cf.ComfyError, match="no 'FancyNode' node"):
        cf.Comfy().run(sd_graph(), 30)


def test_addresses_are_tidied():
    assert cf.clean_url('192.168.1.20:8188/') == 'http://192.168.1.20:8188'
    assert cf.clean_url('') == cf.DEFAULT_URL and cf.clean_url(' https://c.example/x/ ') == 'https://c.example/x'


def test_nothing_answering_says_how_to_fix_it(server):
    import requests
    server.down = requests.exceptions.ConnectionError('refused')
    with pytest.raises(cf.ComfyError, match='--listen'):
        cf.Comfy('192.168.1.20:8188').stats()
    with pytest.raises(cf.ComfyError) as e:
        cf.Comfy('http://127.0.0.1:8188').stats()
    assert '--listen' not in str(e.value) and 'Start ComfyUI' in str(e.value)


# -- waiting -------------------------------------------------------------------------------

def test_a_failed_run_is_one_sentence(server):
    server.finish = False
    pid = cf.Comfy().queue(sd_graph())
    server.history[pid] = {'status': {'status_str': 'error', 'completed': False, 'messages': [
        ['execution_start', {}],
        ['execution_error', {'node_type': 'KSampler', 'exception_message': 'CUDA out of memory\n', 'traceback': ['x' * 9000]}]]}}
    with pytest.raises(cf.ComfyError) as e:
        cf.Comfy().wait(pid, 30)
    assert 'KSampler' in str(e.value) and 'ran out of memory' in str(e.value) and len(str(e.value)) < 300


def test_a_job_comfy_lost_ends_the_wait_early(server):
    server.finish = False
    c = cf.Comfy()
    pid = c.queue(sd_graph())
    with pytest.raises(cf.ComfyError, match='no longer has this job'):
        c.wait(pid, 3600)
    assert cf._now() < 60


def test_a_timeout_takes_the_job_out_of_comfy(server):
    server.finish = False
    c = cf.Comfy()
    pid = c.queue(sd_graph())
    server.running = [pid]
    with pytest.raises(cf.ComfyError, match='longer than 20 seconds'):
        c.wait(pid, 20)
    posts = [x for x in server.calls if x[0] == 'POST']
    assert ('POST', '/queue', {'delete': [pid]}) in posts and ('POST', '/interrupt', {'prompt_id': pid}) in posts


def test_a_timeout_never_interrupts_somebody_elses_job(server):
    server.finish = False
    c = cf.Comfy()
    pid = c.queue(sd_graph())
    server.running, server.pending = ['theirs'], [pid]
    with pytest.raises(cf.ComfyError):
        c.wait(pid, 20)
    assert not [x for x in server.calls if x[:2] == ('POST', '/interrupt')]


def test_the_wait_ends_whatever_the_poll_interval(server):
    server.finish = False
    c = cf.Comfy()
    pid = c.queue(sd_graph())
    server.pending = [pid]
    with pytest.raises(cf.ComfyError, match='longer than'):
        c.wait(pid, 5, poll=0)


def test_outputs_are_sorted_by_what_they_are():
    items = cf.outputs_of({
        '1': {'images': [{'filename': 'p.png', 'subfolder': '', 'type': 'temp'}]},
        '2': {'images': [{'filename': 'clip.mp4', 'subfolder': 'v', 'type': 'output'}], 'animated': [True]},
        '3': {'gifs': [{'filename': 'vhs.webm', 'type': 'output'}], 'audio': [{'filename': 's.flac', 'type': 'output'}]},
        '4': {'images': [{'filename': 'a.webp', 'type': 'output'}], 'animated': [True]},
        '5': {'images': [{'filename': 'still.png', 'type': 'output'}], 'text': ['hello']}})
    assert [(i['filename'], i['kind']) for i in items] == [
        ('clip.mp4', 'video'), ('vhs.webm', 'video'), ('s.flac', 'audio'), ('a.webp', 'file'), ('still.png', 'image')]
    only_preview = cf.outputs_of({'1': {'images': [{'filename': 'p.png', 'type': 'temp'}]}})
    assert [i['filename'] for i in only_preview] == ['p.png']


# -- the tools -----------------------------------------------------------------------------

def test_an_image_comes_back_as_an_image(comfy, tool, server):
    cf.write('a-first', sd_graph())
    cf.write('z-default', sd_graph())
    result, ok = run(tool, prompt='a red apple', seed=0, _settings={'default_workflow': 'z-default'})
    assert ok and result['images'][0]['media_type'] == 'image/jpeg'
    assert "workflow 'z-default'" in result['text'] and 'seed 0' in result['text']
    assert server.graphs[0]['7']['inputs']['text'] == 'a red apple' and server.graphs[0]['3']['inputs']['seed'] == 0


def test_a_see_through_image_stays_see_through(comfy, tool, server):
    cf.write('w', sd_graph())
    server.files['out.png'] = png('RGBA', (10, 20, 30, 0))
    result, ok = run(tool, prompt='x')
    assert ok and result['images'][0]['media_type'] == 'image/png'


def test_a_video_is_kept_and_played_never_sent_as_an_image(comfy, tool, server, monkeypatch):
    cf.write('clip', video_graph())
    server.outputs = {'save': {'images': [{'filename': 'clip_00001_.mp4', 'subfolder': 'video', 'type': 'output'}], 'animated': [True]}}
    server.files['clip_00001_.mp4'] = b'\x00\x00\x00\x18ftypmp42' + b'v' * 500
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([png(), png(), png()], 5.0))
    result, ok = run(tool, prompt='she waves')
    assert ok
    kept = list(cf.OUTPUT_DIR.iterdir())
    assert len(kept) == 1 and kept[0].suffix == '.mp4' and kept[0].read_bytes().endswith(b'v' * 500)
    assert f'/api/plugin/comfyui/file/{kept[0].name}' in result['text'] and '<!--FILES:' in result['text']
    assert 'a 5 s clip' in result['text'] and '3 frames' in result['text']
    assert [i['media_type'] for i in result['images']] == ['image/jpeg']          # the frames, not the mp4


def test_a_video_without_ffmpeg_still_reaches_the_user(comfy, tool, server, monkeypatch):
    cf.write('clip', video_graph())
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b'mp4'
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    monkeypatch.setattr(cf, 'has_ffmpeg', lambda: False)
    result, ok = run(tool, prompt='x')
    assert ok and isinstance(result, str) and '<!--FILES:' in result and 'no ffmpeg' in result
    monkeypatch.setattr(cf, 'has_ffmpeg', lambda: True)             # there, but the clip beat it: say that instead
    assert "ffmpeg couldn't read the clip" in run(tool, prompt='x')[0]


def test_several_images_come_back_as_a_sheet_with_handles(comfy, tool, server, monkeypatch):
    from core import images as ci
    cf.write('w', sd_graph())
    server.outputs = {'9': {'images': [{'filename': 'out.png', 'type': 'output'}, {'filename': 'two.png', 'type': 'output'}]}}
    server.files['two.png'] = png(color=(0, 0, 200))
    handles = iter(['img:aaa.jpg', 'img:bbb.jpg'])
    monkeypatch.setattr(ci, 'stash', lambda raw, *a, **k: next(handles))
    result, ok = run(tool, prompt='x')
    assert ok and len(result['images']) == 1 and '2 images' in result['text']
    assert '1. img:aaa.jpg' in result['text'] and '2. img:bbb.jpg' in result['text']


def test_an_input_image_is_uploaded_and_wired_in(comfy, tool, server, monkeypatch):
    from core import images as ci
    cf.write('clip', video_graph())
    cf.write('plain', sd_graph())
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: SimpleNamespace(data=png()))
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    result, ok = run(tool, prompt='x', workflow='clip', image='img:abc.jpg')
    assert ok and 'Your picture is 64x48: the result takes its shape.' in result['text']
    sent = server.graphs[0]
    assert server.uploads and sent['img']['inputs']['image'] == server.uploads[0]
    assert (sent['i2v']['inputs']['width'], sent['i2v']['inputs']['height']) == (736, 544)      # 4:3, the pixels it had
    result, ok = run(tool, prompt='x', workflow='clip', image='img:abc.jpg', shape='portrait')  # her word beats the picture
    assert ok and 'Your picture' not in result['text'] and server.graphs[1]['i2v']['inputs']['width'] == 512
    result, ok = run(tool, prompt='x', workflow='plain', image='img:abc.jpg')
    assert not ok and 'takes no input image' in result and len(server.graphs) == 2


def test_tool_failures_are_words(comfy, tool, server):
    assert 'No ComfyUI workflows' in run(tool, prompt='x')[0]
    cf.write('one', sd_graph())
    result, ok = run(tool, prompt='x', workflow='../../etc/passwd')
    assert not ok and 'Installed: one' in result
    assert run(tool, prompt='  ') == ('No prompt provided', False)
    (cf.WORKFLOW_DIR / 'broken.json').write_text('{not json')
    result, ok = run(tool, prompt='x', workflow='broken')
    assert not ok and "can't be read" in result
    server.outputs = {}
    result, ok = run(tool, prompt='x', workflow='one')
    assert not ok and 'handed back no file' in result


def test_the_list_tells_her_what_each_workflow_is(comfy, tool):
    cf.write('portrait', sd_graph(), {'description': 'Faces, 1024 square'})
    cf.write('clip', video_graph())
    (cf.WORKFLOW_DIR / 'broken.json').write_text('[]')
    cf.write('fixed', _img2img({}))
    text, ok = tool.execute('comfy_list_workflows', {}, None, {'default_workflow': 'portrait'})
    assert ok and 'portrait (default): image, square. Faces, 1024 square' in text
    assert 'clip: video, takes an input image, landscape' in text and 'broken: unusable' in text
    assert 'fixed: image, takes an input image, fixed shape' in text


def test_waits_are_bounded(tool):
    assert tool._seconds('0', 300) == 10 and tool._seconds(None, 300) == 300 and tool._seconds('0.5', 300) == 10
    assert tool._int(True) is None and tool._int('7') == 7 and tool._int('x') is None


# -- the panel's doors ---------------------------------------------------------------------

@pytest.fixture
def api(server):
    import plugins.comfyui.routes.api as routes
    return routes


def _upload(api, name, data, given=None, replace=False):
    async def read(size=-1):
        return data if size < 0 else data[:size]
    form = {'file': SimpleNamespace(filename=name, read=read)}
    if given:
        form['name'] = given
    if replace:
        form['replace'] = '1'

    async def get_form():
        return form
    return asyncio.run(api.upload(request=SimpleNamespace(headers={'content-length': str(len(data))}, form=get_form)))


def test_upload_keeps_a_workflow_and_a_replace_keeps_its_notes(comfy, api):
    got = _upload(api, 'My Flow.json', json.dumps(sd_graph()).encode())
    assert got['name'] == 'My-Flow' and not got['replaced'] and got['workflow']['kind'] == 'image'
    assert api.update(name='My-Flow', body={'description': 'for faces', 'prompt_node': '6'})['workflow']['prompt_node'] == '6'
    body, status = _upload(api, 'my-flow.json', json.dumps(flux_graph()).encode())
    assert status == 409 and 'already here' in body['error'] and set(cf.read(cf.find('My-Flow')[1])[0]) == set(sd_graph())
    got = _upload(api, 'my-flow.json', json.dumps(flux_graph()).encode(), replace=True)
    assert got == {**got, 'name': 'My-Flow', 'replaced': True} and got['workflow']['description'] == 'for faces'
    assert [w['name'] for w in api.list_workflows()['workflows']] == ['My-Flow']


def test_upload_refuses_in_words(comfy, api):
    body, status = _upload(api, 'ui.json', json.dumps({'nodes': [], 'links': []}).encode())
    assert status == 400 and 'editor format' in body['error']
    assert _upload(api, 'x.png', png())[1] == 400
    assert not cf.workflows()


def test_import_takes_the_newest_run_that_worked(comfy, api, server):
    ok = {'status_str': 'success', 'completed': True}
    server.history = {'a': {'prompt': [1, 'a', flux_graph()], 'status': ok},
                      'b': {'prompt': [2, 'b', sd_graph()], 'status': ok},
                      'c': {'prompt': [3, 'c', video_graph()], 'status': {'status_str': 'error'}}}
    got = api.import_last(body={'name': 'from comfy'}, settings={})
    assert got['name'] == 'from-comfy' and set(cf.read(cf.find('from-comfy')[1])[0]) == set(sd_graph())
    assert api.import_last(body={'name': ''}, settings={})[1] == 400
    server.history = {}
    assert 'no finished run' in api.import_last(body={'name': 'x'}, settings={})[0]['error']


def test_check_answers_in_lines(comfy, api, server):
    cf.write('w', sd_graph())
    server.info = {c: {} for c in ('KSampler', 'EmptyLatentImage', 'CLIPTextEncode', 'VAEDecode', 'SaveImage')}
    got = api.check(name='w', body={'url': '10.0.0.5:8188'}, settings={})
    assert not got['ok'] and got['lines'] == ['Missing custom node: CheckpointLoaderSimple']
    server.info['CheckpointLoaderSimple'] = {'input': {'required': {'ckpt_name': [['x/sdxl.safetensors'], {}]}}}
    got = api.check(name='w', body={}, settings={'comfy_url': '10.0.0.5:8188'})
    assert got['ok'] and got['lines'][0].startswith('Ready. ComfyUI at http://10.0.0.5:8188') and 'will be read as' in got['lines'][1]
    assert api.check(name='nope', body={}, settings={})[1] == 404


def test_test_says_what_answered(api, server):
    got = api.test(body={'url': '10.0.0.5:8188'}, settings={})
    assert got == {'ok': True, 'url': 'http://10.0.0.5:8188', 'text': 'ComfyUI 0.46.0 on linux: NVIDIA Test, 32 GB'}
    import requests
    server.down = requests.exceptions.ConnectTimeout()
    got = api.test(body={'url': '10.0.0.5:8188'}, settings={})
    assert not got['ok'] and '--listen' in got['text'] and 'port 8188' in got['text']    # silent = same causes as refused
    got = api.test(body={'url': 'localhost:8188'}, settings={})
    assert not got['ok'] and "didn't answer in time" in got['text']


def test_clips_are_served_by_name_only_and_can_be_cleared(comfy, api, tmp_path):
    dest = cf.keep('my flow', '.mp4')
    dest.write_bytes(b'clip')
    (tmp_path / 'secret.mp4').write_bytes(b'no')
    assert dest.name.startswith('my-flow-') and cf.kept(dest.name) == dest
    for bad in ('../secret.mp4', '..', '.hidden', 'a/b.mp4', '', None, str(tmp_path / 'secret.mp4')):
        assert cf.kept(bad) is None
    assert api.file(name='../secret.mp4')[1] == 404 and api.file(name=dest.name).path == str(dest)
    assert api.list_workflows()['outputs'] == {'count': 1, 'bytes': 4}
    assert api.clear_outputs() == {'deleted': 1, 'left': 0} and cf.saved() == (0, 0)


def test_delete_and_download(comfy, api):
    cf.write('w', sd_graph(), {'description': 'd'})
    assert json.loads(api.download(name='w').body)[cf.META] == {'description': 'd'}
    assert api.delete(name='w') == {'deleted': 'w'} and api.delete(name='w')[1] == 404


# == the bug hunt of 2026-11-21: each test is a finding a scout made ==========================

# -- the prompt, on real workflow shapes ---------------------------------------------------

def _detailer(g, face='detailed face, sharp eyes'):
    """A face detailer after the main pass: it works on the decoded picture with its own prompt."""
    g.update({'fp': node('CLIPTextEncode', 'Face prompt', text=face, clip=['4', 1]),
              'fn': node('CLIPTextEncode', text='', clip=['4', 1]),
              'fd': node('FaceDetailer', image=['8', 0], model=['4', 0], positive=['fp', 0], negative=['fn', 0], seed=4),
              '9': node('SaveImage', images=['fd', 0])})
    return g


def test_a_detailer_keeps_its_own_prompt():
    g, notes = cf.apply(_detailer(sd_graph()), {}, 'a knight', 5)
    assert g['7']['inputs']['text'] == 'a knight' and g['fp']['inputs']['text'] == 'detailed face, sharp eyes'
    assert g['fd']['inputs']['seed'] == 5 and not [n for n in notes if 'prompt' in n]


def test_flux_with_a_detailer_still_gets_its_main_prompt():
    g = flux_graph()
    g.update({'dec': node('VAEDecode', samples=['5', 0], vae=['9', 0]),
              'fp': node('CLIPTextEncode', text='detailed face', clip=['9', 0]),
              'fd': node('FaceDetailer', image=['dec', 0], positive=['fp', 0], negative=['fp', 0], seed=1),
              '8': node('SaveImage', images=['fd', 0])})
    out, _ = cf.apply(g, {}, 'a fox', 5)
    assert out['1']['inputs']['text'] == 'a fox' and out['fp']['inputs']['text'] == 'detailed face'


def test_a_leftover_branch_is_not_part_of_the_workflow():
    g, notes = cf.apply(hires_graph(), {}, 'a cat', 5)
    assert g['7']['inputs']['text'] == 'a cat' and g['20']['inputs']['text'] == 'unused'
    assert g['3']['inputs']['seed'] == 5 and g['11']['inputs']['seed'] == 5 and g['22']['inputs']['seed'] == 9
    assert '20' not in [n['id'] for n in cf.describe(hires_graph())['prompt_nodes']]


def test_a_prompt_built_from_string_nodes_is_followed_and_said():
    g = sd_graph()
    g.update({'style': node('PrimitiveString', 'Style', value='oil painting'),
              'subj': node('PrimitiveStringMultiline', 'Subject prompt', value='a cat'),
              'cat': node('StringConcatenate', string_a=['style', 0], string_b=['subj', 0], delimiter=', ')})
    g['7']['inputs']['text'] = ['cat', 0]
    out, notes = cf.apply(g, {}, 'a dog', 1)
    assert out['subj']['inputs']['value'] == 'a dog' and out['style']['inputs']['value'] == 'oil painting'
    assert any("went into 'Subject prompt'" in n and "'Style'" in n for n in notes)
    ids = [n['id'] for n in cf.describe(g)['prompt_nodes']]
    assert 'style' in ids and 'subj' in ids                     # the panel can offer what the tracer found
    out, notes = cf.apply(g, {'prompt_node': 'style'}, 'a dog', 1)
    assert out['style']['inputs']['value'] == 'a dog' and out['subj']['inputs']['value'] == 'a cat'
    assert not [n for n in notes if 'went into' in n]


def test_several_positive_prompts_combined_are_said():
    g = sd_graph()
    g.update({'r': node('CLIPTextEncode', 'Region', text='a deer', clip=['4', 1]),
              'c': node('ConditioningCombine', conditioning_1=['7', 0], conditioning_2=['r', 0])})
    g['3']['inputs']['positive'] = ['c', 0]
    out, notes = cf.apply(g, {}, 'a wolf', 1)
    assert out['7']['inputs']['text'] == 'a wolf' and out['r']['inputs']['text'] == 'a deer'
    assert any("'Region'" in n for n in notes)


def test_audio_and_dual_guider_shapes_are_understood():
    ace = {'t': node('TextEncodeAceStepAudio', clip=['c', 0], tags='old tags', lyrics='la la'),
           'z': node('ConditioningZeroOut', conditioning=['t', 0]),
           'k': node('KSampler', positive=['t', 0], negative=['z', 0], latent_image=['l', 0], seed=1),
           's': node('SaveAudio', audio=['k', 0]), 'p': node('PreviewImage', images=['k', 0])}
    g, _ = cf.apply(ace, {}, 'synthwave', 2)
    assert g['t']['inputs'] == {'clip': ['c', 0], 'tags': 'synthwave', 'lyrics': 'la la'}
    assert cf.kind(ace) == 'audio'                              # a preview beside it doesn't make it an image job
    dual = {'a': node('CLIPTextEncode', text='old', clip=['c', 0]), 'n': node('CLIPTextEncode', text='bad', clip=['c', 0]),
            'g': node('DualCFGGuider', model=['m', 0], cond1=['a', 0], cond2=['a', 0], negative=['n', 0]),
            'k': node('SamplerCustomAdvanced', guider=['g', 0], latent_image=['l', 0]), 's': node('SaveImage', images=['k', 0])}
    g, _ = cf.apply(dual, {}, 'new', 2)
    assert g['a']['inputs']['text'] == 'new' and g['n']['inputs']['text'] == 'bad'


def test_a_seed_typed_in_a_number_node_is_set():
    g = sd_graph()
    g['si'] = node('PrimitiveInt', value=7)
    g['3']['inputs']['seed'] = ['si', 0]
    out, notes = cf.apply(g, {}, 'x', 99)
    assert out['si']['inputs']['value'] == 99 and not [n for n in notes if 'seed' in n]


# -- the size ------------------------------------------------------------------------------

def test_a_shape_carries_through_an_upscale_and_skips_a_leftover_branch():
    g = hires_graph()
    assert cf.reshape(g, 'landscape')
    size = lambda n: (g[n]['inputs']['width'], g[n]['inputs']['height'])
    assert size('5') == (1248, 832) and size('10') == (2496, 1664)             # both passes, each its own pixels
    assert size('21') == (512, 512)                                             # runs never, touched never
    assert cf.size_plan(hires_graph())[1] is True


def ratio_graph():
    """ComfyUI's LTX template: a ratio list feeds Width and Height number nodes, halved by math
    nodes on the way to the latent, and a x2 upsampler after the first pass."""
    g = video_graph()
    g.update({
        'pick': node('ResolutionSelector', aspect_ratio='16:9 (Widescreen)', megapixels=0.9, multiple=32),
        'w': node('PrimitiveInt', 'Width', value=['pick', 0]), 'h': node('PrimitiveInt', 'Height', value=['pick', 1]),
        'w2': node('ComfyMathExpression', **{'expression': 'a/2', 'values.a': ['w', 0]}),
        'h2': node('ComfyMathExpression', **{'expression': 'a/2', 'values.a': ['h', 0]}),
        'empty': node('EmptyLTXVLatentVideo', width=['w2', 1], height=['h2', 1], length=121, batch_size=1),
        'i2v': node('LTXVImgToVideoInplace', vae=['v', 0], image=['img', 0], latent=['empty', 0], strength=0.7),
        'guide': node('CFGGuider', model=['u', 0], positive=['cond', 0], negative=['cond', 1], cfg=1.0),
        'samp': node('SamplerCustomAdvanced', noise=['noise', 0], guider=['guide', 0], latent_image=['i2v', 0]),
        'up': node('LTXVLatentUpsampler', samples=['samp', 0], vae=['v', 0]),
        'dec': node('VAEDecode', samples=['up', 0], vae=['v', 0]),
    })
    return g


RATIOS = ['1:1 (Square)', '2:3 (Portrait Photo)', '3:2 (Photo)', '9:16 (Portrait Widescreen)', '16:9 (Widescreen)']


def test_a_ratio_list_gets_the_nearest_choice_that_comfy_offers():
    offers = lambda cls, key: RATIOS if (cls, key) == ('ResolutionSelector', 'aspect_ratio') else []
    assert cf.describe(ratio_graph())['shape'] == 'landscape'
    for want, picked in ((1.0, '1:1 (Square)'), (1024 / 1100, '1:1 (Square)'), ('portrait', '9:16 (Portrait Widescreen)'),
                         (0.7, '2:3 (Portrait Photo)'), ('landscape', '16:9 (Widescreen)'), (3.0, '16:9 (Widescreen)')):
        g = ratio_graph()
        assert cf.reshape(g, want, offers) and g['pick']['inputs']['aspect_ratio'] == picked, want
        assert g['empty']['inputs']['width'] == ['w2', 1] and g['w']['inputs']['value'] == ['pick', 0]   # the wiring stays
    g = ratio_graph()
    assert cf.reshape(g, 1.0) is False and cf.reshape(g, 1.0, lambda cls, key: []) is False     # a list nobody can read
    assert g['pick']['inputs']['aspect_ratio'] == '16:9 (Widescreen)'
    sizes = lambda cls, key: ['512x512', '1024x1024 (1.0 MP)', '1344x768', '768x1344']
    preset = sd_graph()
    preset['5'] = node('SizePicker', resolution='1344x768')
    assert cf.reshape(preset, 'square', sizes) and preset['5']['inputs']['resolution'] == '1024x1024 (1.0 MP)'


def test_a_size_typed_in_number_nodes_is_set_there():
    g = ratio_graph()
    del g['pick']
    g['w']['inputs']['value'], g['h']['inputs']['value'] = 1280, 704
    assert cf.reshape(g, 'portrait') and (g['w']['inputs']['value'], g['h']['inputs']['value']) == (704, 1280)
    assert g['empty']['inputs']['width'] == ['w2', 1]
    one = ratio_graph()                             # both sides wired to one plain number: that is no size
    del one['pick']
    one['w']['inputs']['value'] = one['h']['inputs']['value'] = 1024
    one['empty']['inputs']['height'] = ['w2', 1]
    assert cf.reshape(one, 'portrait') is False and cf.describe(one)['shape'] == ''
    tiny = sd_graph()
    tiny['5']['inputs'].update(width=2, height=3)   # numbers, but not pixels
    assert cf.reshape(tiny, 'square') is False


# -- the input image -----------------------------------------------------------------------

def _img2img(extra):
    g = sd_graph()
    g.update({'30': node('LoadImage', image='photo.png'), '31': node('VAEEncode', pixels=['30', 0], vae=['4', 2])})
    g['3']['inputs']['latent_image'] = ['31', 0]
    g.update(extra)
    return g


def test_her_image_never_goes_into_a_reference_or_a_mask():
    pose = _img2img({'1': node('LoadImage', image='pose.png'),
                     '2': node('ControlNetApplyAdvanced', positive=['7', 0], negative=['6', 0], control_net=['cn', 0], image=['1', 0])})
    pose['3']['inputs'].update(positive=['2', 0], negative=['2', 1])
    g, notes = cf.apply(pose, {}, 'x', 1, image='mine.png')
    assert g['30']['inputs']['image'] == 'mine.png' and g['1']['inputs']['image'] == 'pose.png'
    assert g['7']['inputs']['text'] == 'x' and not [n for n in notes if 'input image went' in n]

    scaled = _img2img({'1': node('LoadImage', image='style.png'), '2': node('ImageScale', image=['1', 0], width=224, height=224),
                       '5b': node('IPAdapterAdvanced', model=['4', 0], image=['2', 0])})
    assert cf.image_target(scaled) == '30'                      # a reference stays one behind a resize

    mask = _img2img({'1': node('LoadImageMask', image='mask.png', channel='alpha'),
                     '2': node('LoadImage', image='mask2.png'), '32': node('SetLatentNoiseMask', samples=['31', 0], mask=['2', 1])})
    mask['3']['inputs']['latent_image'] = ['32', 0]
    assert cf.image_target(mask) == '30' and [n['id'] for n in cf.describe(mask)['image_nodes']] == ['2', '30']
    resized = _img2img({'33': node('ResizeImageMaskNode', input=['30', 0])})     # core's resize node: a name, not a mask
    resized['31']['inputs']['pixels'] = ['33', 0]
    assert cf.image_target(resized) == '30'

    only_pose = sd_graph()
    only_pose.update({'1': node('LoadImage', image='pose.png'),
                      '2': node('ControlNetApply', conditioning=['7', 0], control_net=['cn', 0], image=['1', 0])})
    only_pose['3']['inputs']['positive'] = ['2', 0]
    assert cf.describe(only_pose)['takes_image'] is False
    assert cf.describe(only_pose, {'image_node': '1'})['takes_image'] is True     # unless a person says it is hers


def test_two_possible_input_images_are_said_and_none_passed_is_said():
    two = _img2img({'40': node('LoadImage', 'Second picture', image='b.png'), '41': node('ImageBlend', image1=['30', 0], image2=['40', 0]),
                    '31': node('VAEEncode', pixels=['41', 0], vae=['4', 2])})
    g, notes = cf.apply(two, {}, 'x', 1, image='mine.png')
    assert g['30']['inputs']['image'] == 'mine.png' and any("also loads 'Second picture'" in n for n in notes)
    g, notes = cf.apply(_img2img({}), {}, 'x', 1)
    assert g['30']['inputs']['image'] == 'photo.png' and any("none was passed" in n and 'photo.png' in n for n in notes)
    upscale = {'1': node('LoadImage', image='a.png'), '2': node('ImageScaleBy', image=['1', 0]), '3': node('SaveImage', images=['2', 0])}
    with pytest.raises(cf.ComfyError, match='works on a picture'):
        cf.apply(upscale, {}, 'x', 1)


# -- refusals and odd replies --------------------------------------------------------------

def test_a_second_refusal_is_fixed_too_and_a_third_is_said(server):
    second = _refusal(['xl/sdxl.safetensors'])
    second['node_errors'] = {'4b': second['node_errors']['4']}
    second['node_errors']['4b']['errors'][0]['extra_info'].update(received_value='b.safetensors', input_config=[['x/b.safetensors'], {}])
    g = sd_graph()
    g['4b'] = node('LoraLoader', lora_name='b.safetensors', model=['4', 0])
    server.refuse = [_refusal(['xl/sdxl.safetensors']), second]
    outputs, fixed = cf.Comfy().run(g, 30)
    assert [f['to'] for f in fixed] == ['xl/sdxl.safetensors', 'x/b.safetensors'] and len(server.graphs) == 3
    server.refuse = [_refusal(['sdxl.safetensors'])]             # "fixing" it to what it already is: no loop, words
    with pytest.raises(cf.ComfyError, match="isn't there"):
        cf.Comfy().run(sd_graph(), 30)


def test_a_job_taken_with_a_broken_branch_is_a_refusal_not_a_run(server):
    server.partial = [_refusal(['other.safetensors'])['node_errors']]
    with pytest.raises(cf.ComfyError, match="'sdxl.safetensors' isn't there"):
        cf.Comfy().run(sd_graph(), 30)
    assert ('POST', '/queue', {'delete': ['job1']}) in server.calls and not server.history.get('job2')


def test_replies_in_the_wrong_shape_become_words_not_crashes(server):
    assert cf.outputs_of(['x']) == [] and cf.outputs_of(None) == []
    odd = cf.outputs_of({'1': {'images': [3, {'filename': ['a']}, {'filename': 'ok.png', 'subfolder': None, 'type': 7}], 'animated': True},
                         '2': 'nope', '3': {'images': 'abc'}})
    assert odd == [{'filename': 'ok.png', 'subfolder': '', 'type': 'output', 'kind': 'file'}]
    assert 'gave no reason' in cf._failure({'messages': [['one'], 'two', ['execution_error', 'three']]})
    assert cf.fix_rejection(sd_graph(), {'error': 'no prompt', 'node_errors': []}) == ([], ['no prompt'])
    assert cf.fix_rejection(sd_graph(), {'error': {'extra_info': 'x'}, 'node_errors': {'4': 'x', '3': {'errors': ['y']}}})[1]
    server.history = ['not', 'a', 'dict']
    with pytest.raises(cf.ComfyError, match='no finished run'):
        cf.Comfy().last_run()
    server.finish = False
    c = cf.Comfy()
    pid = c.queue(sd_graph())
    server.history, server.running = {pid: 'garbage'}, [pid]
    with pytest.raises(cf.ComfyError, match='longer than'):      # an unreadable entry is not a finished job
        c.wait(pid, 8)


def test_an_unreadable_wait_stops_the_job(server, monkeypatch):
    c = cf.Comfy()
    monkeypatch.setattr(c, 'wait', lambda pid, timeout: (_ for _ in ()).throw(TypeError('odd')))
    with pytest.raises(cf.ComfyError, match="couldn't read"):
        c.run(sd_graph(), 30)
    assert ('POST', '/queue', {'delete': ['job1']}) in server.calls


def test_one_bad_file_never_takes_the_list_down(comfy, tool, server):
    import plugins.comfyui.routes.api as api
    cf.write('good', sd_graph())
    cf.WORKFLOW_DIR.joinpath('nan.json').write_text('{"5": {"class_type": "EmptyLatentImage", "inputs": {"width": NaN}}}')
    cf.WORKFLOW_DIR.joinpath('ids.json').write_text(json.dumps({'²': node('KSampler', seed=1), '9' * 30: node('SaveImage')}))
    rows = {r['name']: r for r in api.list_workflows()['workflows']}
    assert 'error' in rows['nan'] and 'kind' in rows['good'] and 'kind' in rows['ids']
    assert 'nan: unusable' in tool.execute('comfy_list_workflows', {}, None, {})[0]
    body, status = _upload(api, 'inf.json', b'{"5": {"class_type": "EmptyLatentImage", "inputs": {"width": Infinity}}}')
    assert status == 400 and not cf.find('inf')[1]
    deep = ('[' * 5000 + ']' * 5000).encode()
    assert _upload(api, 'deep.json', deep)[1] == 400


def test_a_replaced_workflow_forgets_the_picked_nodes(comfy, server):
    import plugins.comfyui.routes.api as api
    _upload(api, 'w.json', json.dumps(sd_graph()).encode())
    api.update(name='w', body={'description': 'faces', 'prompt_node': '7', 'image_node': '30'})
    swapped = sd_graph()
    swapped['6'], swapped['7'] = swapped['7'], swapped['6']       # same ids, the other way round
    swapped['3']['inputs'].update(positive=['6', 0], negative=['7', 0])
    got = _upload(api, 'w.json', json.dumps(swapped).encode(), replace=True)
    assert got['workflow']['description'] == 'faces' and got['workflow']['prompt_node'] == '6'
    assert cf.read(cf.find('w')[1])[1] == {'description': 'faces'}


def test_names_with_nothing_to_keep_stay_apart(comfy, server):
    import plugins.comfyui.routes.api as api
    a, b = cf.slug('猫'), cf.slug('犬')
    assert a != b and a.startswith('workflow-') and cf.slug('猫') == a
    _upload(api, '猫.json', json.dumps(sd_graph()).encode())
    _upload(api, '犬.json', json.dumps(flux_graph()).encode())
    assert len(cf.workflows()) == 2


# -- privacy -------------------------------------------------------------------------------

def test_a_private_chat_sends_nothing_off_this_network(comfy, tool, server, monkeypatch):
    from core import images as ci
    cf.write('clip', video_graph())
    monkeypatch.setattr(tool, '_private', lambda: True)
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: SimpleNamespace(data=png()))
    result, ok = run(tool, prompt='x', image='https://evil.example/x.png?d=secret')
    assert not ok and 'already here' in result and not server.calls
    result, ok = run(tool, prompt='x', _settings={'comfy_url': 'https://gpu.example.com'})
    assert not ok and 'outside this network' in result and not server.calls
    assert run(tool, prompt='x', image='img:abc.jpg', _settings={'comfy_url': '192.168.1.20:8188'})[1]
    monkeypatch.setattr(tool, '_private', lambda: False)
    assert run(tool, prompt='x', image='https://example.com/a.png')[1]      # an open chat may fetch one


def test_a_password_in_the_address_is_never_quoted_back(server):
    import requests
    server.down = requests.exceptions.ConnectionError('refused')
    c = cf.Comfy('https://krem:hunter2@comfy.example.com')
    with pytest.raises(cf.ComfyError) as e:
        c.stats()
    assert 'hunter2' not in str(e.value) and 'krem' not in str(e.value) and 'comfy.example.com' in str(e.value)
    assert 'hunter2' in c.url                                   # the request itself still carries it


def test_only_media_is_kept_under_its_own_type(comfy, server):
    import plugins.comfyui.routes.api as api
    assert cf.keep('w', '.html').suffix == '.bin' and cf.keep('w', '.svg').suffix == '.bin' and cf.keep('w', '.MP4').suffix == '.mp4'
    assert cf.keep('w', '.mp4') != cf.keep('w', '.mp4')         # two runs in one second never share a file
    page = cf.keep('w', '.xhtml')
    page.write_bytes(b'<script>alert(1)</script>')
    sent = api.file(name=page.name)
    assert sent.media_type == 'application/octet-stream' and 'attachment' in sent.headers['content-disposition']
    clip = cf.keep('w', '.ogg')
    clip.write_bytes(b'x')
    sent = api.file(name=clip.name)
    assert sent.media_type == 'audio/ogg' and 'content-disposition' not in sent.headers


def test_clearing_clips_leaves_one_still_being_written(comfy):
    done = cf.keep('w', '.mp4')
    done.write_bytes(b'12')
    writing = done.with_name(f'.{done.name}.tmp')
    writing.write_bytes(b'half')
    assert cf.saved() == (1, 2) and cf.clear_saved() == (1, 0)
    assert writing.exists() and not done.exists()


def test_what_goes_wrong_reaches_her_without_paths_or_python(comfy, tool, server, monkeypatch):
    cf.write('w', sd_graph())
    monkeypatch.setattr(cf.Comfy, 'run', lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError(2, 'No such file', str(cf.OUTPUT_DIR))))
    result, ok = run(tool, prompt='x')
    assert not ok and str(cf.OUTPUT_DIR) not in result and 'FileNotFoundError' in result
    server.files['out.png'] = b''
    monkeypatch.undo()


def test_arguments_she_gets_wrong_are_answered(comfy, tool, server):
    cf.write('w', sd_graph())
    result, ok = run(tool, prompt='x', shape='widescreen')
    assert not ok and 'square, portrait, landscape' in result and not server.graphs     # said before anything runs
    result, ok = run(tool, prompt='x', shape=' Portrait ')
    assert ok and server.graphs[0]['5']['inputs']['height'] == 1248 and 'an image (64x48)' in result['text']
    result, ok = run(tool, prompt='x', width=1344, height=768)                           # the arguments it used to take
    assert ok and "read as shape='landscape'" in result['text'] and server.graphs[1]['5']['inputs']['width'] == 1248
    result, ok = run(tool, prompt='x', width='1024px', height=768)
    assert ok and 'Pass shape instead' in result['text'] and server.graphs[2]['5']['inputs']['height'] == 1024
    assert run(tool, prompt=['a', 'b']) == ('The prompt has to be text.', False)
    assert run(tool, prompt='x', seed=float('inf'))[1] and run(tool, prompt='x', workflow=7)[1]


def test_an_empty_file_from_comfy_is_not_a_clip(comfy, tool, server):
    cf.write('clip', video_graph())
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b''
    result, ok = run(tool, prompt='x')
    assert not ok and 'empty file' in result and cf.saved() == (0, 0)
    assert not list(cf.OUTPUT_DIR.glob('.*'))


def test_a_shape_nobody_can_set_is_said_with_her_pictures_size(comfy, tool, server, monkeypatch):
    from core import images as ci
    cf.write('fixed', _img2img({}))
    cf.write('ratio', ratio_graph())
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: SimpleNamespace(data=png(size=(512, 512))))
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([png(size=(1280, 704))], 5.0))
    result, ok = run(tool, prompt='x', workflow='fixed', image='img:a')
    assert ok and "shape can't be set from here" in result['text'] and 'Your picture is 512x512' in result['text']
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: SimpleNamespace(data=png(size=(640, 480))))
    result, ok = run(tool, prompt='x', workflow='fixed', image='img:a')                # it came out her picture's shape anyway
    assert ok and "can't be set" not in result['text'] and 'an image (64x48)' in result['text']
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: SimpleNamespace(data=png(size=(512, 512))))
    result, ok = run(tool, prompt='x', workflow='fixed', shape='square')
    assert ok and "shape can't be set from here, so it kept its own." in result['text'] and 'Your picture' not in result['text']
    server.info = {'ResolutionSelector': {'input': {'required': {'aspect_ratio': ['COMBO', {'options': RATIOS}]}}}}
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b'mp4'
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a')        # the run that went wrong for her
    assert ok and server.graphs[-1]['pick']['inputs']['aspect_ratio'] == '1:1 (Square)'
    assert "a 5 s clip" in result['text'] and '(1280x704)' in result['text'] and 'takes its shape' in result['text']
    server.info = {}                                                                    # a ComfyUI that won't list them
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a')
    assert ok and "shape can't be set from here" in result['text']
    assert server.graphs[-1]['pick']['inputs']['aspect_ratio'] == '16:9 (Widescreen)'
