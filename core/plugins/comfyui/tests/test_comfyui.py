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
            if self.finish:
                self.history[pid] = {'prompt': [len(self.graphs), pid, kw['json']['prompt']],
                                     'status': {'status_str': 'success', 'completed': True, 'messages': []},
                                     'outputs': self.outputs}
            if self.partial:                         # the real one runs what it could check, and says what it dropped
                return Reply(body={'prompt_id': pid, 'number': 1, 'node_errors': self.partial.pop(0)})
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
    server.running = [pid]
    with pytest.raises(cf.ComfyError, match='longer than'):
        c.wait(pid, 5, poll=0)


def test_time_spent_waiting_its_turn_is_not_the_jobs_time(server, monkeypatch):
    """Her long video is rendering and a still is queued behind it: the still's five minutes
    start when ComfyUI starts on it."""
    server.finish = False
    c = cf.Comfy()
    pid = c.queue(sd_graph())
    t0 = cf._now()
    monkeypatch.setattr(c, '_where', lambda p: 'pending' if cf._now() - t0 < 500 else 'running')
    with pytest.raises(cf.ComfyError, match='longer than 20 seconds'):
        c.wait(pid, 20)
    assert 515 <= cf._now() - t0 <= 525                 # 500 s in the queue, then its own 20
    forever = cf.Comfy()
    pid = forever.queue(sd_graph())
    server.pending = [pid]
    with pytest.raises(cf.ComfyError, match='never got to this job'):
        forever.wait(pid, 20)
    assert ('POST', '/queue', {'delete': [pid]}) in server.calls


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


def test_she_is_told_whether_a_kept_file_is_in_the_backups(comfy, tool, server, api, monkeypatch):
    cf.write('clip', video_graph())
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b'mp4'
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    off = run(tool, prompt='x')[0]
    assert 'left out of' in off and 'a restore does not bring them back' in off and cf.PANEL in off
    on = run(tool, prompt='x', _settings={'backup_outputs': True})[0]
    assert "goes into Sapphire's backups" in on and 'left out of' not in on
    # the panel and the page read the same answer, and off is what ships
    assert api.list_workflows()['outputs'] == {'count': 2, 'bytes': 6, 'backed_up': False}
    got = api.outputs(settings={'backup_outputs': True})
    assert (got['count'], got['bytes'], got['backed_up']) == (2, 6, True) and len(got['files']) == 2
    manifest = json.loads((Path(cf.__file__).parent / 'plugin.json').read_text(encoding='utf-8'))
    assert [f['default'] for f in manifest['capabilities']['settings'] if f['key'] == 'backup_outputs'] == [False]


def test_a_picture_she_makes_says_nothing_about_backups(comfy, tool, server):
    cf.write('still', sd_graph())                    # a still goes to the chat's own picture store, which is backed up
    server.outputs = {'9': {'images': [{'filename': 'a.png', 'type': 'output'}]}}
    server.files['a.png'] = png()
    result, ok = run(tool, prompt='a cat')
    assert ok and 'backups' not in (result['text'] if isinstance(result, dict) else result)


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
    assert ok and 'portrait (default): image, square by default. Faces, 1024 square' in text
    assert 'clip: video, takes an input image, landscape by default' in text and 'broken: unusable' in text
    assert 'fixed: image, takes an input image, shape not settable' in text


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
    assert api.list_workflows()['outputs'] == {'count': 1, 'bytes': 4, 'backed_up': False}
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


def test_a_resize_between_two_passes_takes_the_shape_with_the_pass_before_it():
    """Hires through pixels: decode, resize to a typed size, encode, sample again. Shaping only the
    resize would squeeze a square first pass into a wide frame."""
    g = sd_graph()
    g.update({
        '10': node('ImageScale', image=['8', 0], width=2048, height=2048, upscale_method='lanczos', crop='disabled'),
        '11': node('VAEEncode', pixels=['10', 0], vae=['4', 2]),
        '12': node('KSampler', model=['4', 0], positive=['7', 0], negative=['6', 0], latent_image=['11', 0], seed=2, denoise=0.4),
        '13': node('VAEDecode', samples=['12', 0], vae=['4', 2]),
        '9': node('SaveImage', images=['13', 0]),
    })
    assert cf.reshape(g, 'landscape')
    size = lambda n: (g[n]['inputs']['width'], g[n]['inputs']['height'])
    assert size('5') == (1248, 832) and size('10') == (2496, 1664)
    # a picture resized on its way INTO an image-to-video node is that node's business: the walk ends at the node
    clip = video_graph()
    clip['fit'] = node('ImageScale', image=['img', 0], width=768, height=512, upscale_method='lanczos', crop='center')
    clip['i2v']['inputs']['image'] = ['fit', 0]
    assert cf.reshape(clip, 'portrait')
    assert (clip['i2v']['inputs']['width'], clip['i2v']['inputs']['height']) == (512, 768) and clip['fit']['inputs']['width'] == 768
    # plain image-to-image: the resize of her picture is the only size there is
    edit = _img2img({'32': node('ImageScale', image=['30', 0], width=1024, height=1024, upscale_method='lanczos', crop='center')})
    edit['31']['inputs']['pixels'] = ['32', 0]
    assert cf.reshape(edit, 'landscape') and edit['32']['inputs']['width'] == 1248


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
    assert g['30']['inputs']['image'] == 'mine.png' and notes == ["Slot 2 of this workflow can't be left empty, so its saved picture ('b.png') went in too."]
    g, notes = cf.apply(_img2img({}), {}, 'x', 1)
    assert g['30']['inputs']['image'] == 'photo.png' and any(n.startswith(cf.NO_PICTURE) and 'photo.png' in n for n in notes)
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
    api.update(name='w', body={'description': 'faces', 'prompt_node': '7', 'image_node': '30', 'extras': {'3:steps': {'max': 30}}})
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
    for old, word in (({'width': 1344, 'height': 768}, "pass shape='landscape'"), ({'width': '1024px', 'height': 768}, 'pass shape (square'),
                      ({'width': 1024}, 'pass shape (square'), ({'shape': 1}, 'shape is one of'), ({'shape': ['portrait']}, 'shape is one of')):
        result, ok = run(tool, prompt='x', **old)                                        # the arguments it used to take: said, never run
        assert not ok and word in result and len(server.graphs) == 1, old
    result, ok = run(tool, prompt='x', extras=[])                                        # an empty extras of any kind is no extras
    assert ok and len(server.graphs) == 2
    result, ok = run(tool, prompt='x', extras={'Steps': 10 ** 400})
    assert not ok and 'Steps takes a whole number' in result
    assert run(tool, prompt=['a', 'b']) == ('The prompt has to be text.', False)
    assert run(tool, prompt='x', seed=float('inf'))[1]
    assert run(tool, prompt='x', workflow=7) == ('workflow is a name from comfy_list_workflows.', False)


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
    sent = len(server.graphs)
    result, ok = run(tool, prompt='x', workflow='fixed', shape='square')               # her own word, no place for it: before the render
    assert not ok and "makes its own shape" in result and 'without shape' in result and len(server.graphs) == sent
    server.info = {'ResolutionSelector': {'input': {'required': {'aspect_ratio': ['COMBO', {'options': RATIOS}]}}}}
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b'mp4'
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a')        # the run that went wrong for her
    assert ok and server.graphs[-1]['pick']['inputs']['aspect_ratio'] == '1:1 (Square)'
    # the frames stood in for the clip here are 1280x704: the note goes by what came out, not by what was set
    assert "a 5 s clip" in result['text'] and '(1280x704)' in result['text'] and 'takes its shape' not in result['text']
    assert 'Your picture is 512x512 and the nearest shape this workflow makes is 1280x704' in result['text']
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([png(size=(960, 960))], 5.0))
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a')
    assert ok and 'Your picture is 512x512: the result takes its shape.' in result['text']
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a', shape='portrait')
    assert ok and server.graphs[-1]['pick']['inputs']['aspect_ratio'] == '9:16 (Portrait Widescreen)'
    assert 'Asked for portrait and it came out 960x960' in result['text']                 # a workflow may ignore what was set
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([png(size=(704, 1280))], 5.0))
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a', shape='portrait')
    assert ok and 'Your picture is 512x512 and you asked for portrait: it is cropped or squeezed to fit.' in result['text']
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([png(size=(960, 960))], 5.0))
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))                       # no ffmpeg: nothing measured, the plan is said
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a')
    assert ok and 'Your picture is 512x512: the result takes its shape.' in result
    server.info = {}                                                                    # a ComfyUI that won't list them
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a')
    assert ok and "shape can't be set from here" in result
    assert server.graphs[-1]['pick']['inputs']['aspect_ratio'] == '16:9 (Widescreen)'
    sent = len(server.graphs)
    result, ok = run(tool, prompt='she waves', workflow='ratio', image='img:a', shape='square')
    assert not ok and 'makes its own shape' in result and len(server.graphs) == sent


# -- her extras ----------------------------------------------------------------------------

def knob_graph():
    """ComfyUI's template style: the author's own controls are titled number and switch nodes."""
    g = ratio_graph()
    g.update({
        'dur': node('PrimitiveInt', 'Duration', value=5), 'rate': node('PrimitiveInt', 'Frame Rate', value=24),
        'len': node('ComfyMathExpression', **{'expression': 'a * b + 1', 'values.a': ['dur', 0], 'values.b': ['rate', 0]}),
        't2v': node('PrimitiveBoolean', 'Switch to Text to Video?', value=False),
        'enh': node('PrimitiveBoolean', 'Boolean (Enable Prompt Enhance)', value=False),
        'plain': node('PrimitiveInt', value=7), 'int': node('PrimitiveInt', 'Int', value=3),      # no name of their own
        'seedn': node('PrimitiveInt', 'Seed', value=42),                                          # the tool's, not hers
        'spare': node('PrimitiveInt', 'Leftover', value=9),                                       # wired to nothing
    })
    g['empty']['inputs']['length'] = ['len', 1]
    g['cond']['inputs']['frame_rate'] = ['rate', 0]
    g['i2v']['inputs'].update(bypass=['t2v', 0], extra=['enh', 0], a=['plain', 0], b=['int', 0])
    g['noise']['inputs']['noise_seed'] = ['seedn', 0]
    return g


def test_a_workflows_own_controls_are_found_and_the_tools_are_not():
    line = lambda g, meta=None: cf.controls_line(cf.controls(g, meta))
    assert line(knob_graph()) == 'Duration 5 (1 to 20), Enable Prompt Enhance off, Frame Rate 24 (6 to 96), Switch to Text to Video off'
    assert line(sd_graph()) == 'Steps 20 (5 to 80), Images 1 (1 to 4)'                 # seed, width, height: never
    assert line(video_graph()) == 'Frame Rate 24 (6 to 96), Frames 121 (30 to 484)'    # and no batch of videos
    assert line(_img2img({})) == 'Steps 20 (5 to 80)'
    edit = _img2img({})
    edit['3']['inputs'].update(denoise=0.6, cfg=4.5)
    assert line(edit) == 'Steps 20 (5 to 80), CFG 4.5 (1.125 to 18), Denoise 0.6 (0.15 to 1)'
    edit['3']['inputs'].update(denoise=1.0, cfg=1.0)                                    # a full denoise and a distilled cfg 1 are not settings
    assert line(edit) == 'Steps 20 (5 to 80)'
    detail = _detailer(sd_graph())
    detail['fd']['inputs'].update(steps=8, cfg=3.0)                                     # a detailer keeps its own
    assert line(detail) == 'Steps 20 (5 to 80), Images 1 (1 to 4)'
    typed = sd_graph()                                                                  # a size typed in number nodes is the shape's
    typed.update({'w': node('PrimitiveInt', 'Width', value=1024), 'h': node('PrimitiveInt', 'Height', value=768)})
    typed['5']['inputs'].update(width=['w', 0], height=['h', 0])
    assert line(typed) == 'Steps 20 (5 to 80), Images 1 (1 to 4)'


def test_one_name_in_two_places_moves_together_and_two_values_are_two_controls():
    g = video_graph()
    g['save'] = node('CreateVideo', images=['dec', 0], fps=24)
    g['out'] = node('SaveVideo', video=['save', 0])
    rate = next(c for c in cf.controls(g) if c['name'] == 'Frame Rate')
    assert rate['places'] == [('cond', 'frame_rate'), ('save', 'fps')]
    assert cf.set_extras(g, {}, {'frame rate': 30}) == ["Frame Rate 30 (the workflow's own is 24)."]
    assert g['cond']['inputs']['frame_rate'] == 30 and g['save']['inputs']['fps'] == 30
    two = hires_graph()
    two['3']['inputs']['steps'], two['11']['inputs']['steps'] = 20, 10
    assert [c['name'] for c in cf.controls(two)] == ['Steps', 'Images', 'Steps #2']
    cf.set_extras(two, {}, {'steps #2': 12})
    assert (two['3']['inputs']['steps'], two['11']['inputs']['steps']) == (20, 12)


def test_extras_are_checked_before_they_are_written():
    g = knob_graph()
    notes = cf.set_extras(g, {}, {'duration': 8, 'FRAME_RATE': '30', 'switch to text to video': 'on', 'Enable Prompt Enhance': None})
    assert notes == ["Duration 8 (the workflow's own is 5).", "Frame Rate 30 (the workflow's own is 24).",
                     "Switch to Text to Video on (the workflow's own is off)."]
    assert (g['dur']['inputs']['value'], g['rate']['inputs']['value'], g['t2v']['inputs']['value']) == (8, 30, True)
    assert cf.set_extras(knob_graph(), {}, {'Duration': 5}) == [] and cf.set_extras(knob_graph(), {}, '{"Duration": 6}')
    for bad, words in (({'steps': 9}, "has no 'steps' to set. It has: Duration 5 (1 to 20)"), ({'Duration': 60}, 'goes from 1 to 20'),
                       ({'Duration': 2.5}, 'whole number'), ({'Duration': 'long'}, 'whole number'), ({'Duration': True}, 'whole number'),
                       ({'Duration': float('nan')}, 'whole number'), ({'Enable Prompt Enhance': 'maybe'}, 'true or false'),
                       ('nope', 'name: value pairs'), ([1], 'name: value pairs')):
        with pytest.raises(cf.ComfyError, match=words.replace('(', r'\(').replace(')', r'\)')):
            cf.set_extras(knob_graph(), {}, bad)
    bare = {'1': node('LoadImage', image='a.png'), '2': node('SaveImage', images=['1', 0])}
    with pytest.raises(cf.ComfyError, match='It has no extras'):
        cf.set_extras(bare, {}, {'Steps': 4})


def test_a_person_names_limits_and_hides_a_control(comfy, api):
    cf.write('clip', knob_graph())
    row = lambda: api.list_workflows()['workflows'][0]
    assert [(c['id'], c['name'], c['auto']) for c in row()['extras']][0] == ('dur:value', 'Duration', [1, 20])
    api.update(name='clip', body={'extras': {'dur:value': {'name': 'Seconds', 'max': 10}}})
    api.update(name='clip', body={'extras': {'t2v:value': {'off': True, 'min': 'x', 'junk': 1}}})
    g, meta = cf.read(cf.find('clip')[1])
    assert meta['extras'] == {'dur:value': {'name': 'Seconds', 'max': 10}, 't2v:value': {'off': True}}
    assert cf.controls_line(cf.controls(g, meta)) == 'Seconds 5 (1 to 10), Enable Prompt Enhance off, Frame Rate 24 (6 to 96)'
    assert cf.set_extras(g, meta, {'seconds': 10}) and g['dur']['inputs']['value'] == 10
    for bad in ({'Seconds': 12}, {'Duration': 8}, {'Switch to Text to Video': True}):
        with pytest.raises(cf.ComfyError):
            cf.set_extras(cf.read(cf.find('clip')[1])[0], meta, bad)
    api.update(name='clip', body={'extras': {'dur:value': {}}})                          # an empty row: as found again
    assert cf.read(cf.find('clip')[1])[1]['extras'] == {'t2v:value': {'off': True}}
    assert row()['extras'][0]['name'] == 'Duration' and [c['off'] for c in row()['extras'] if c['id'] == 't2v:value'] == [True]


def test_she_sees_the_extras_in_the_list_and_sets_them_in_a_call(comfy, tool, server):
    cf.write('clip', knob_graph())
    cf.write('bare', {'1': node('LoadImage', image='a.png'), '2': node('SaveImage', images=['1', 0])})
    text, ok = tool.execute('comfy_list_workflows', {}, None, {})
    assert ok and '\n      extras: Duration 5 (1 to 20), Enable Prompt Enhance off' in text and 'extras are optional' in text
    assert text.count('extras: ') == 1
    result, ok = run(tool, prompt='she waves', workflow='clip', extras={'Duration': 8})
    assert ok and "Duration 8 (the workflow's own is 5)." in result['text'] and server.graphs[0]['dur']['inputs']['value'] == 8
    result, ok = run(tool, prompt='she waves', workflow='clip', extras={'Duration': 99})
    assert not ok and 'goes from 1 to 20' in result and len(server.graphs) == 1         # refused before anything ran
    result, ok = run(tool, prompt='she waves', workflow='clip', extras={})
    assert ok and server.graphs[1]['dur']['inputs']['value'] == 5
    cf.WORKFLOW_DIR.joinpath('bare.json').unlink()
    cf.WORKFLOW_DIR.joinpath('clip.json').unlink()
    cf.write('none', {'1': node('CLIPTextEncode', text='x'), '2': node('KSampler', positive=['1', 0], seed=1), '3': node('SaveImage', images=['2', 0])})
    assert 'extras' not in tool.execute('comfy_list_workflows', {}, None, {})[0]


# -- found by the hunt of 2026-11-22 -------------------------------------------------------

def test_core_resize_node_between_two_passes_takes_the_shape_too():
    """ComfyUI's own resize node types its sides as `resize_type.width` / `resize_type.height`."""
    g = sd_graph()
    g.update({
        '10': node('ResizeImageMaskNode', **{'input': ['8', 0], 'resize_type': 'scale dimensions', 'resize_type.width': 2048,
                                             'resize_type.height': 2048, 'resize_type.crop': 'disabled', 'scale_method': 'lanczos'}),
        '11': node('VAEEncode', pixels=['10', 0], vae=['4', 2]),
        '12': node('KSampler', model=['4', 0], positive=['7', 0], negative=['6', 0], latent_image=['11', 0], seed=2, denoise=0.4),
        '13': node('VAEDecode', samples=['12', 0], vae=['4', 2]), '9': node('SaveImage', images=['13', 0]),
    })
    assert cf.reshape(g, 'landscape')
    assert (g['5']['inputs']['width'], g['5']['inputs']['height']) == (1248, 832)
    assert (g['10']['inputs']['resize_type.width'], g['10']['inputs']['resize_type.height']) == (2496, 1664)
    only = _img2img({'32': node('ResizeImageMaskNode', **{'input': ['30', 0], 'resize_type': 'scale dimensions',
                                                          'resize_type.width': 1024, 'resize_type.height': 1024})})
    only['31']['inputs']['pixels'] = ['32', 0]
    assert cf.describe(only)['shape'] == 'square' and cf.reshape(only, 'portrait') and only['32']['inputs']['resize_type.height'] == 1248
    longer = _img2img({'32': node('ResizeImageMaskNode', **{'input': ['30', 0], 'resize_type': 'scale longer dimension',
                                                            'resize_type.longer_size': 1536})})
    longer['31']['inputs']['pixels'] = ['32', 0]
    assert cf.describe(longer)['shape'] == ''                                    # keeps the picture's own shape: nothing to set


def test_typed_sizes_follow_the_ratio_a_list_landed_on():
    g = ratio_graph()                                # a ratio list on the first pass, a typed resize on the second
    g.update({'fit': node('ImageScale', image=['dec', 0], width=2048, height=1152, upscale_method='lanczos', crop='disabled'),
              'enc': node('VAEEncode', pixels=['fit', 0], vae=['v', 0]),
              'samp2': node('SamplerCustomAdvanced', noise=['noise', 0], guider=['guide', 0], latent_image=['enc', 0]),
              'dec2': node('VAEDecode', samples=['samp2', 0], vae=['v', 0]), 'save': node('SaveVideo', video=['dec2', 0])})
    assert cf.reshape(g, 1.25, lambda cls, key: RATIOS)          # her picture is 5:4, the list only has 3:2
    assert g['pick']['inputs']['aspect_ratio'] == '3:2 (Photo)'
    w, h = g['fit']['inputs']['width'], g['fit']['inputs']['height']
    assert abs(w / h - 1.5) < 0.03, (w, h)                       # 3:2 as well, not her 5:4: the two passes agree
    blind = ratio_graph()
    blind.update({k: copy_of for k, copy_of in g.items() if k in ('fit', 'enc', 'samp2', 'dec2', 'save')})
    blind['fit']['inputs'].update(width=2048, height=1152)
    blind['pick']['inputs']['aspect_ratio'] = '16:9 (Widescreen)'
    assert cf.reshape(blind, 1.0) is False and blind['fit']['inputs']['width'] == 2048   # a list nobody can read: nothing is touched


def test_a_ratio_list_wins_over_sides_typed_beside_it_and_a_turn_switch_is_used():
    cr = sd_graph()                                  # a size picker that keeps width/height for its "custom" entry only
    cr['5'] = node('CR SDXL Aspect Ratio', width=1024, height=1024, aspect_ratio='16:9 landscape 1344x768', batch_size=1)
    offers = lambda cls, key: ['1:1 square 1024x1024', '3:4 portrait 896x1152', '16:9 landscape 1344x768', '9:16 portrait 768x1344']
    assert cf.describe(cr)['shape'] == 'landscape'
    assert cf.reshape(cr, 'portrait', offers) and cr['5']['inputs']['aspect_ratio'] == '9:16 portrait 768x1344'
    assert cr['5']['inputs']['width'] == 1024
    custom = sd_graph()                              # the same picker on "custom", wired into a plain latent: its typed sides count
    custom.update({'cr': node('CR SDXL Aspect Ratio', width=1024, height=1024, aspect_ratio='custom', batch_size=1)})
    custom['5']['inputs'].update(width=['cr', 0], height=['cr', 1])
    assert cf.reshape(custom, 'landscape') and (custom['cr']['inputs']['width'], custom['cr']['inputs']['height']) == (1248, 832)
    kj = sd_graph()                                  # a list of wide sizes only, with a switch that lays one on its side
    kj['5'] = node('EmptyLatentImagePresets', dimensions='1024 x 576 (1.778:1)', invert=False, batch_size=1)
    wide = lambda cls, key: ['512 x 512 (1:1)', '768 x 512 (1.5:1)', '1024 x 576 (1.778:1)', '1536 x 640 (2.4:1)']
    assert cf.reshape(kj, 'portrait', wide) and kj['5']['inputs'] == {'dimensions': '1024 x 576 (1.778:1)', 'invert': True, 'batch_size': 1}
    assert cf.describe(kj)['shape'] == 'portrait'
    assert cf.reshape(kj, 'landscape', wide) and kj['5']['inputs']['invert'] is False
    assert cf.reshape(kj, 0.66, wide) and (kj['5']['inputs']['dimensions'], kj['5']['inputs']['invert']) == ('768 x 512 (1.5:1)', True)
    assert cf._ratio('1344X768') == (1344.0, 768.0) and cf._ratio('9' * 400 + ':1') is None


def test_a_sampler_that_types_its_own_size_or_takes_it_under_another_name_is_found():
    wrap = {'e': node('WanVideoEmptyEmbeds', width=832, height=480, num_frames=81),
            's': node('WanVideoSampler', model=['m', 0], image_embeds=['e', 0], steps=30, cfg=6.0, seed=1),
            'd': node('WanVideoDecode', samples=['s', 0], vae=['v', 0]), 'o': node('SaveVideo', video=['d', 0])}
    assert cf.describe(wrap)['shape'] == 'landscape' and cf.reshape(wrap, 'portrait')
    assert (wrap['e']['inputs']['width'], wrap['e']['inputs']['height']) == (480, 832)
    own = {'s': node('HyVideoSampler', model=['m', 0], width=960, height=544, num_frames=73, steps=30, seed=1),
           'd': node('VAEDecode', samples=['s', 0], vae=['v', 0]), 'o': node('SaveVideo', video=['d', 0])}
    assert cf.reshape(own, 'square') and own['s']['inputs']['width'] == own['s']['inputs']['height'] == 736
    flux = sd_graph()                                # a model-side node with a width and height of its own is never a size
    flux['20'] = node('ModelSamplingFlux', model=['4', 0], width=1024, height=1024, max_shift=1.15, base_shift=0.5)
    flux['3']['inputs']['model'] = ['20', 0]
    assert cf.reshape(flux, 'landscape') and flux['20']['inputs']['width'] == 1024 and len(cf.size_plan(flux)[0]) == 1


def test_a_size_read_off_another_picture_is_not_typed_anywhere():
    g = video_graph()                                # width and height come from a picture's own size, behind a pass with a Steps node
    g.update({'st': node('PrimitiveInt', 'Steps', value=30), 'gs': node('GetImageSize', image=['first', 0]),
              'first': node('VAEDecode', samples=['pre', 0], vae=['v', 0]),
              'pre': node('KSampler', positive=['pos', 0], negative=['neg', 0], latent_image=['el', 0], steps=['st', 0], seed=1),
              'el': node('EmptyLatentImage', width=1024, height=576, batch_size=1)})
    g['i2v']['inputs'].update(width=['gs', 0], height=['gs', 1], image=['first', 0])
    assert cf._typed_at(g, 'i2v', 'width', 'width') is None
    assert cf.reshape(g, 'portrait') and (g['el']['inputs']['width'], g['el']['inputs']['height']) == (576, 1024)
    assert g['st']['inputs']['value'] == 30
    deep = sd_graph()                                # a wide web of pass-through nodes is walked once, not once per path
    for i in range(8):
        deep[f'm{i}'] = node('ComfyMathExpression', expression='a', **{f'values.{c}': [f'm{i + 1}', 0] for c in 'abcdefghijklmn'})
    deep['m8'] = node('PrimitiveInt', 'W', value=1024)
    deep['5']['inputs']['width'] = ['m0', 0]
    assert cf._typed_at(deep, '5', 'width', 'width') == ('m8', 'value')
    huge = sd_graph()
    huge['5']['inputs']['width'] = 10 ** 400         # a number no size can be: no crash, no place
    assert cf.describe(huge)['shape'] == '' and cf.reshape(huge, 'square') is False


def test_steps_shared_by_two_samplers_are_not_hers_to_change():
    split = sd_graph()                               # two models share one run and hand over at step 10
    split['3'] = node('KSamplerAdvanced', model=['4', 0], positive=['7', 0], negative=['6', 0], latent_image=['5', 0],
                      noise_seed=1, steps=20, cfg=3.5, start_at_step=0, end_at_step=10)
    split['11'] = node('KSamplerAdvanced', model=['4', 0], positive=['7', 0], negative=['6', 0], latent_image=['3', 0],
                       noise_seed=1, steps=20, cfg=3.5, start_at_step=10, end_at_step=10000)
    split['8']['inputs']['samples'] = ['11', 0]
    assert cf.controls_line(cf.controls(split)) == 'CFG 3.5 (0.875 to 14), Images 1 (1 to 4)'
    whole = sd_graph()                               # one advanced sampler doing the whole run: steps are hers
    whole['3'] = node('KSamplerAdvanced', model=['4', 0], positive=['7', 0], negative=['6', 0], latent_image=['5', 0],
                      noise_seed=1, steps=20, cfg=3.5, start_at_step=0, end_at_step=10000)
    assert 'Steps 20 (5 to 80)' in cf.controls_line(cf.controls(whole))
    sig = flux_graph()                               # a scheduler whose steps are cut in two further on
    sig.update({'9': node('BasicScheduler', model=['m', 0], scheduler='simple', steps=20, denoise=1.0),
                '10': node('SplitSigmas', sigmas=['9', 0], step=12)})
    sig['5']['inputs']['sigmas'] = ['10', 0]
    assert 'Steps' not in cf.controls_line(cf.controls(sig))
    plain = flux_graph()
    plain['9'] = node('BasicScheduler', model=['m', 0], scheduler='simple', steps=20, denoise=1.0)
    assert 'Steps 20 (5 to 80)' in cf.controls_line(cf.controls(plain))


def test_a_title_keeps_its_note_and_a_short_name_still_reaches_it():
    g = knob_graph()
    g['dur']['_meta']['title'], g['rate']['_meta']['title'] = 'Duration (seconds)', 'Shift (5)'
    assert cf.controls_line(cf.controls(g)).startswith('Duration (seconds) 5 (1 to 20), Enable Prompt Enhance off, Shift (5) 24')
    assert cf.set_extras(g, {}, {'duration': 8}) == ["Duration (seconds) 8 (the workflow's own is 5)."]   # the start of one name
    two = knob_graph()
    two['dur']['_meta']['title'], two['rate']['_meta']['title'] = 'Frames in', 'Frames out'
    with pytest.raises(cf.ComfyError, match="more than one 'frames'"):
        cf.set_extras(two, {}, {'frames': 8})
    assert cf.set_extras(two, {}, {'frames out': 30}) == ["Frames out 30 (the workflow's own is 24)."]


def test_names_in_any_script_reach_their_own_control():
    g = knob_graph()
    g['dur']['_meta']['title'], g['rate']['_meta']['title'] = '\u65f6\u957f', '\u5e27\u7387'
    assert cf.set_extras(g, {}, {'\u5e27\u7387': 30}) == ["\u5e27\u7387 30 (the workflow's own is 24)."]
    assert (g['dur']['inputs']['value'], g['rate']['inputs']['value']) == (5, 30)
    for nothing in ('', '???', '\U0001f3ac'):
        with pytest.raises(cf.ComfyError, match='to set'):
            cf.set_extras(knob_graph(), {}, {nothing: 9})
    marks = knob_graph()
    marks['plain']['_meta'] = {'title': '!!!'}       # a title with nothing to call it by is no control
    assert '!!!' not in cf.controls_line(cf.controls(marks))


def test_a_titled_number_node_obeys_the_rules_of_the_input_it_feeds():
    g = sd_graph()
    g.update({'c': node('PrimitiveFloat', 'CFG', value=1.0), 'd': node('PrimitiveFloat', 'Denoise', value=1.0),
              's': node('PrimitiveInt', 'Steps', value=20), 'sd': node('PrimitiveInt', 'Seed', value=123456),
              'm': node('ComfyMathExpression', **{'expression': 'a+1', 'values.a': ['sd', 0]}),
              'w': node('PrimitiveInt', 'Width', value=1344),
              'hm': node('ComfyMathExpression', **{'expression': 'a*9/16', 'values.a': ['w', 0]})})
    g['3']['inputs'].update(cfg=['c', 0], denoise=['d', 0], steps=['s', 0], seed=['m', 0])
    g['5']['inputs'].update(width=['w', 0], height=['hm', 0])
    assert cf.controls_line(cf.controls(g)) == 'Images 1 (1 to 4), Steps 20 (5 to 80)'     # the node's title, the input's limits
    g['s']['inputs']['value'] = 100
    assert 'Steps 100 (25 to 150)' in cf.controls_line(cf.controls(g))
    face = _detailer(sd_graph())
    face['ds'] = node('PrimitiveInt', 'Detailer Steps', value=20)
    face['fd']['inputs']['steps'] = ['ds', 0]
    assert 'Detailer' not in cf.controls_line(cf.controls(face))


def test_a_person_cannot_widen_a_control_past_its_input_or_name_two_alike():
    g = sd_graph()
    meta = {'extras': {'3:steps': {'min': -5, 'max': 100000}, '5:batch_size': {'max': 500, 'name': 'Steps'}}}
    found = cf.controls(g, meta)
    assert [(c['name'], c['min'], c['max']) for c in found] == [('Steps', 1, 150), ('Images #2', 1, 4)]
    with pytest.raises(cf.ComfyError):
        cf.set_extras(sd_graph(), meta, {'Steps': -5})
    assert cf.clean_meta({'extras': {'a': {'min': 10 ** 400, 'max': 1e12, 'name': 'x' * 90}}})['extras']['a'] == {'name': 'x' * 37 + '...'}


def test_a_file_full_of_controls_shows_her_twelve_and_never_breaks_the_list(comfy, api):
    g = sd_graph()
    for i in range(40):
        g[f'p{i:02}'] = node('PrimitiveInt', f'Knob {i:02}', value=20 + i)
        g[f'u{i:02}'] = node('SomeNode', n=[f'p{i:02}', 0])
        g['9']['inputs'][f'x{i:02}'] = [f'u{i:02}', 0]
    line = cf.controls_line(cf.controls(g))
    assert line.count(' to ') == 12 and 'Knob 09' in line and 'Knob 12' not in line and len(line) < 400
    with pytest.raises(cf.ComfyError, match="no 'Knob 30'"):
        cf.set_extras(g, {}, {'Knob 30': 40})
    g['big'] = node('PrimitiveFloat', 'Strength', value=1e308)   # a number no setting can be
    g['9']['inputs']['big'] = ['big', 0]
    cf.write('many', g)
    row = api.list_workflows()['workflows'][0]
    assert 'error' not in row and 'Strength' not in [c['name'] for c in row['extras']]
    json.dumps(row, allow_nan=False)                 # the panel's list must always be sendable
    junk = {f'gone{i}:value': {'off': True} for i in range(70)}
    api.update(name='many', body={'extras': junk})
    api.update(name='many', body={'extras': {'p00:value': {'max': 30}}})
    assert cf.read(cf.find('many')[1])[1]['extras'] == {'p00:value': {'max': 30}}        # rows for nodes that are gone never crowd one out


def test_frames_seconds_and_a_matching_audio_length_are_known():
    g = video_graph()
    g['aud'] = node('LTXVEmptyLatentAudio', frames_number=121, frame_rate=24, batch_size=1)
    g['save']['inputs']['audio'] = ['aud', 0]
    frames = next(c for c in cf.controls(g) if c['name'] == 'Frames')
    assert frames['places'] == [('aud', 'frames_number'), ('i2v', 'length')]             # the two lengths move as one
    song = {'1': node('CLIPTextEncode', text='x'), '2': node('EmptyLatentAudio', seconds=47.6, batch_size=1),
            '3': node('KSampler', positive=['1', 0], latent_image=['2', 0], seed=1, steps=50, cfg=5.0),
            '4': node('VAEDecodeAudio', samples=['3', 0]), '5': node('SaveAudio', audio=['4', 0])}
    assert cf.controls_line(cf.controls(song)) == 'Seconds 47.6 (11.9 to 190.4), Steps 50 (12 to 150), CFG 5 (1.25 to 20)'


def test_one_unreadable_picture_never_costs_her_the_rest(comfy, tool, server, monkeypatch):
    cf.write('clip', video_graph())
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}, {'filename': 'bad.png', 'type': 'output'}],
                               'animated': [True]}}
    server.files.update({'c.mp4': b'mp4', 'bad.png': b'not a picture'})
    server.outputs['save']['animated'] = [False]
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([png()], 5.0))
    result, ok = run(tool, prompt='x')
    assert ok and "a 5 s clip" in result['text'] and "1 picture ComfyUI handed back couldn't be opened and was left out." in result['text']
    assert len(list(cf.OUTPUT_DIR.iterdir())) == 1 and '<!--FILES:' in result['text']
    cf.write('still', sd_graph())
    server.outputs = {'9': {'images': [{'filename': 'out.png', 'type': 'output'}, {'filename': 'bad.png', 'type': 'output'}]}}
    result, ok = run(tool, prompt='x', workflow='still')
    assert ok and 'an image (64x48)' in result['text'] and 'left out' in result['text']
    server.outputs = {'9': {'images': [{'filename': 'bad.png', 'type': 'output'}]}}
    assert run(tool, prompt='x', workflow='still') == ("ComfyUI handed back a picture that can't be opened.", False)


def test_an_upload_is_named_the_same_on_every_system_and_utf16_is_said(comfy, api):
    got = _upload(api, 'C:\\Users\\me\\wan:v2.json', json.dumps(sd_graph()).encode())
    assert got['name'] == 'wan-v2'
    cf.WORKFLOW_DIR.joinpath('pad.json').write_bytes(json.dumps(sd_graph()).encode('utf-16'))
    with pytest.raises(cf.ComfyError, match='UTF-8'):
        cf.read(cf.WORKFLOW_DIR / 'pad.json')


# -- the Apps page: a person runs a workflow -----------------------------------------------

def _run(api, picture=None, **fields):
    """POST run as the page sends it: a form, and maybe a picture file."""
    async def read(size=-1):
        return picture if size < 0 else picture[:size]
    form = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in fields.items()}
    if picture is not None:
        form['image'] = SimpleNamespace(read=read, filename='in.png')
    async def get_form():
        return form
    return asyncio.run(api.run(request=SimpleNamespace(form=get_form, headers={}), settings={'comfy_url': 'http://127.0.0.1:8188'}))


def _finished(api, job):
    import time
    for _ in range(500):
        told = api.run_state(job=job['id'])
        if told['state'] != 'running':
            return told
        time.sleep(0.01)
    raise AssertionError('the run never ended')


def test_the_page_runs_a_workflow_as_a_job_and_keeps_what_it_made(comfy, api, server, monkeypatch):
    api.JOBS.clear()
    cf.write('still', sd_graph())
    cf.write('clip', video_graph())
    job = _run(api, workflow='still', prompt='a red apple', shape='landscape', seed='77', extras={'steps': 30})
    # the job's own thread may finish before the answer is written (the fake ComfyUI is instant): either is true
    assert job['state'] in ('running', 'done') and job['seed'] == 77 and job['workflow'] == 'still'
    done = _finished(api, job)
    sent = server.graphs[0]
    assert done['state'] == 'done' and sent['7']['inputs']['text'] == 'a red apple' and sent['3']['inputs']['seed'] == 77
    assert (sent['5']['inputs']['width'], sent['3']['inputs']['steps']) == (1248, 30)
    assert done['notes'] == ["Steps 30 (the workflow's own is 20)."]
    kept = list(cf.OUTPUT_DIR.iterdir())
    assert len(kept) == 1 and done['files'] == [{'url': f'/api/plugin/comfyui/file/{kept[0].name}', 'name': kept[0].name, 'kind': 'image'}]
    json.dumps(done)                                 # the comfy client and the paths never leave the server
    assert api.outputs()['files'][0] == {'name': kept[0].name, 'kind': 'image', 'url': f'/api/plugin/comfyui/file/{kept[0].name}'}
    # her picture's shape is followed here too, and the note goes by what came out (the frame stood in for the clip)
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b'mp4'
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([png(size=(736, 544))], 5.0))
    done = _finished(api, _run(api, png(), workflow='clip', prompt='she waves'))
    assert done['state'] == 'done' and done['files'][0]['kind'] == 'video' and server.uploads
    assert done['notes'] == ['Your picture is 64x48: the result takes its shape.']
    assert (server.graphs[1]['i2v']['inputs']['width'], server.graphs[1]['i2v']['inputs']['height']) == (736, 544)
    assert api.run_state(job='nope')[1] == 404


def test_the_page_is_told_no_before_anything_is_sent(comfy, api, server):
    api.JOBS.clear()
    cf.write('still', sd_graph())
    cf.write('fixed', _img2img({}))
    for fields, words in (({'workflow': 'still', 'prompt': '  '}, 'Type a prompt'),
                          ({'workflow': 'still', 'prompt': 'x', 'shape': 'wide'}, 'The shape is one of'),
                          ({'workflow': 'still', 'prompt': 'x', 'extras': {'Steps': 9999}}, 'Steps goes from 5 to 80'),
                          ({'workflow': 'still', 'prompt': 'x', 'extras': '{broken'}, "didn't arrive whole"),
                          ({'workflow': 'still', 'prompt': 'x', 'seed': 'abc'}, 'whole number'),
                          ({'workflow': 'gone', 'prompt': 'x'}, "No workflow called 'gone'"),
                          ({'workflow': 'fixed', 'prompt': 'x', 'shape': 'square'}, 'makes its own shape')):
        body, status = _run(api, **fields)
        assert status == 400 and words in body['error'], (fields, body)
    assert 'Call again' not in _run(api, workflow='fixed', prompt='x', shape='square')[0]['error']     # words for a person
    body, status = _run(api, png(), workflow='still', prompt='x')
    assert status == 400 and 'takes no input image' in body['error']
    assert not server.graphs and not server.uploads and not api.JOBS


def test_stop_reaches_comfy_and_a_failure_is_words(comfy, api, server, monkeypatch):
    api.JOBS.clear()
    cf.write('still', sd_graph())
    new = lambda **k: {'id': 'j', 'state': 'running', 'phase': 'sending', 'workflow': 'still', 'seed': 1, 'started': 0.0,
                       'pid': None, 'stop': False, 'comfy': None, **k}
    work = lambda job: api._work(job, sd_graph(), {}, 'x', None, None, [], 30, 'http://127.0.0.1:8188')
    early = new(stop=True)                           # Stop pressed while the pictures were still going out: never queued
    work(early)
    assert early['state'] == 'stopped' and not server.graphs
    server.finish = False                            # ComfyUI takes the job and never finishes it
    late, real = new(), cf.Comfy.queue
    monkeypatch.setattr(cf.Comfy, 'queue', lambda self, g: (late.__setitem__('stop', True), real(self, g))[1])
    work(late)                                       # Stop pressed in the moment ComfyUI was taking it: taken out at once
    assert late['state'] == 'stopped' and late['pid'] == 'job1' and late['phase'] == 'working'
    assert ('POST', '/queue', {'delete': ['job1']}) in server.calls
    monkeypatch.setattr(cf.Comfy, 'queue', real)
    live = new(pid='job9', comfy=cf.Comfy('http://127.0.0.1:8188'))
    api.JOBS['j2'] = live
    assert api.run_stop(job='j2')['state'] == 'running' and live['stop'] is True
    assert ('POST', '/queue', {'delete': ['job9']}) in server.calls
    assert api.run_stop(job='nope')[1] == 404 and 'no longer tracked' in api.run_state(job='nope')[0]['error']
    server.finish, server.outputs = True, {}
    failed = new()
    work(failed)
    assert failed['state'] == 'failed' and 'handed back no file' in failed['error']
    json.dumps(api._told(failed))


def test_a_run_on_the_workflows_own_picture_is_the_first_thing_she_reads(comfy, tool, server, monkeypatch):
    cf.write('clip', video_graph())
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    result, ok = run(tool, prompt='she waves')
    assert ok and result['text'].startswith("No input picture was passed, so this used the workflow's saved one ('start.png'). To use yours, pass image.")
    assert result['text'].count(cf.NO_PICTURE) == 1 and '\nMade an image' in result['text']


def test_every_route_in_the_manifest_has_its_handler(api):
    manifest = json.loads((Path(cf.__file__).parent / 'plugin.json').read_text(encoding='utf-8'))
    for route in manifest['capabilities']['routes']:
        file, name = route['handler'].split(':')
        assert file == 'routes/api.py' and callable(getattr(api, name, None)), route
    assert (Path(cf.__file__).parent / 'app' / 'index.js').is_file() and manifest['capabilities']['app']['label']


# -- an either/or switch on the prompt's way (Krem's Qwen Image 2.1 template, 2026-11-22) --

def switch_graph(switch=False):
    """The picture's prompt comes through a switch: off = the text typed on the switch itself,
    on = that text run through a prompt rewriter first."""
    return {
        'enc': node('TextEncodeQwenImage21', prompt=['show', 0], negative_prompt='', clip=['c', 0]),
        'show': node('PreviewAny', 'Preview as Text', source=['sw', 0]),
        'sw': node('ComfySwitchNode', 'If/Else Switch', switch=switch, on_false='the saved prompt', on_true=['gen', 0]),
        'gen': node('TextGenerate', 'Generate Text', prompt='the saved prompt', clip=['c2', 0], system_prompt=['sys', 0]),
        'sys': node('PrimitiveStringMultiline', 'Text (System Prompt)', value='You rewrite prompts.'),
        'samp': node('KSampler', positive=['enc', 0], negative=['enc', 1], latent_image=['lat', 0], seed=1, steps=25, cfg=1),
        'lat': node('EmptyLatentImage', width=1024, height=1024, batch_size=1),
        'dec': node('VAEDecode', samples=['samp', 0], vae=['v', 0]), 'save': node('SaveImage', images=['dec', 0]),
    }


def test_the_prompt_follows_a_switch_down_the_side_it_is_set_to():
    off, notes = cf.apply(switch_graph(False), {}, 'a red fox', 1)
    assert off['sw']['inputs']['on_false'] == 'a red fox' and off['gen']['inputs']['prompt'] == 'the saved prompt' and notes == []
    assert off['sys']['inputs']['value'] == 'You rewrite prompts.'
    on, notes = cf.apply(switch_graph(True), {}, 'a red fox', 1)
    assert on['gen']['inputs']['prompt'] == 'a red fox' and on['sw']['inputs']['on_false'] == 'the saved prompt' and notes == []
    wired = switch_graph()                           # the switch is set by a titled yes/no node, as ComfyUI's templates do
    wired['pe'] = node('PrimitiveBoolean', 'Boolean (Enable Prompt Enhance)', value=False)
    wired['sw']['inputs']['switch'] = ['pe', 0]
    assert cf.apply(wired, {}, 'a red fox', 1)[0]['sw']['inputs']['on_false'] == 'a red fox'
    wired['pe']['inputs']['value'] = True
    assert cf.apply(wired, {}, 'a red fox', 1)[0]['gen']['inputs']['prompt'] == 'a red fox'
    blind = switch_graph()                           # what sets it can't be read: both sides get it
    blind['sw']['inputs']['switch'] = ['other', 0]
    both = cf.apply(blind, {}, 'a red fox', 1)[0]
    assert both['sw']['inputs']['on_false'] == 'a red fox' and both['gen']['inputs']['prompt'] == 'a red fox'
    assert [n['title'] for n in cf.describe(switch_graph())['prompt_nodes']] == ['If/Else Switch']       # the rewriter is switched off
    assert [n['title'] for n in cf.describe(switch_graph(True))['prompt_nodes']] == ['Generate Text', 'Text (System Prompt)']
    assert cf.apply(switch_graph(True), {'prompt_node': 'sys'}, 'picked', 1)[0]['sys']['inputs']['value'] == 'picked'   # a person's pick still wins
    dead = cf.apply(switch_graph(), {'prompt_node': 'gen'}, 'hers', 1)[0]       # ...unless it is switched off right now: it would swallow the prompt
    assert dead['sw']['inputs']['on_false'] == 'hers' and dead['gen']['inputs']['prompt'] == 'the saved prompt'
    worked_out = switch_graph()                      # the switch is set by a node that works it out: nobody can read that
    worked_out['not'] = node('Logic NOT', boolean=True)
    worked_out['sw']['inputs']['switch'] = ['not', 0]
    both = cf.apply(worked_out, {}, 'hers', 1)[0]
    assert both['sw']['inputs']['on_false'] == 'hers' and both['gen']['inputs']['prompt'] == 'hers' and 'gen' in cf.live(worked_out)


def test_a_setting_that_flips_the_switch_moves_the_prompt_with_it(server):
    g = switch_graph()
    g['pe'] = node('PrimitiveBoolean', 'Boolean (Enable Prompt Enhance)', value=False)
    g['sw']['inputs']['switch'] = ['pe', 0]
    client = cf.Comfy('http://127.0.0.1:8188')
    run, notes, _ = cf.plan(client, g, {}, 'w', 'a red fox', 1, extras={'Enable Prompt Enhance': True})
    assert run['pe']['inputs']['value'] is True and run['gen']['inputs']['prompt'] == 'a red fox'
    assert run['sw']['inputs']['on_false'] == 'the saved prompt' and notes == ["Enable Prompt Enhance on (the workflow's own is off)."]
    assert g['pe']['inputs']['value'] is False and g['gen']['inputs']['prompt'] == 'the saved prompt'      # the stored graph is never touched


# -- Krem's Qwen Image 2.1 edit workflow, imported from a PNG ComfyUI saved (2026-11-22) -----

def edit_graph():
    """Two input pictures into one encoder, a latent taken from the picture (switch off) or an
    empty one (switch on), and a compare viewer whose saved setting ComfyUI reads as a wire."""
    return {
        'a': node('LoadImage', image='person.png'), 'b': node('LoadImage', image='shirt.png'),
        'enc': node('TextEncodeQwenImage21', prompt='old', negative_prompt='', resolution=0, clip=['c', 0], vae=['v', 0],
                    **{'images.image_1': ['a', 0], 'images.image_2': ['b', 0]}),
        'empty': node('EmptyLatentImage', width=1024, height=1024, batch_size=1),
        'lat': node('ComfySwitchNode', switch=False, on_false=['enc', 2], on_true=['empty', 0]),
        'samp': node('KSampler', positive=['enc', 0], negative=['enc', 1], latent_image=['lat', 0], seed=1, steps=25, cfg=1.0),
        'dec': node('VAEDecode', samples=['samp', 0], vae=['v', 0]),
        'save': node('SaveImageAdvanced', images=['dec', 0]),
        'cmp': node('ImageCompare', 'Compare Images', compare_view=['', ''], image_a=['a', 0], image_b=['save', 0]),
    }


def test_a_viewer_comfy_could_not_check_never_stops_the_run(comfy, tool, server):
    lost = lambda outs, cls='ImageCompare': {'cmp': {'class_type': cls, 'dependent_outputs': outs,
                                                      'errors': [{'type': 'exception_during_validation', 'message': 'Exception when validating node'}]}}
    server.partial = [lost(['cmp'])]
    client = cf.Comfy()
    outputs, fixed = client.run(edit_graph(), 30)
    assert outputs and client.left_out == ['Compare Images'] and ('POST', '/queue', {'delete': ['job1']}) not in server.calls
    assert client.said(fixed) == []                  # nothing she can act on: it is in the log, not in her result
    client.run(edit_graph(), 30)                     # the next run starts clean
    assert client.left_out == []
    for bad in (lost(['cmp', 'save']), lost([]), lost(['gone']), {'cmp': 'junk'}, ['junk']):    # a saver lost, or no word on what was
        server.partial = [bad]
        with pytest.raises(cf.ComfyError):
            cf.Comfy().run(edit_graph(), 30)
    cf.write('edit', edit_graph())
    server.partial = [lost(['cmp'])]
    result, ok = run(tool, prompt='swap the shirt')
    assert ok and 'Compare Images' not in result['text']
    preview_only = {k: v for k, v in edit_graph().items() if k != 'save'}      # no saver: a preview IS the result, nothing to spare
    preview_only.update({'pv': node('PreviewImage', images=['dec', 0])})
    preview_only['cmp']['inputs'].pop('image_b')
    server.partial = [{'pv': {'class_type': 'PreviewImage', 'dependent_outputs': ['pv'], 'errors': []}}]
    with pytest.raises(cf.ComfyError):
        cf.Comfy().run(preview_only, 30)
    server.partial = [lost(['samp'])]                # "an output" that feeds other nodes is no viewer
    with pytest.raises(cf.ComfyError):
        cf.Comfy().run(edit_graph(), 30)


def test_the_switched_off_side_of_a_switch_is_not_part_of_the_workflow():
    g = edit_graph()
    assert 'empty' not in cf.live(g) and 'enc' in cf.live(g)
    d = cf.describe(g)
    assert d['shape'] == '' and cf.reshape(g, 'landscape') is False and g['empty']['inputs']['width'] == 1024     # the latent comes from the picture
    assert cf.controls_line(d['extras']) == 'Steps 25 (6 to 100)'                                                # no Images on a latent nobody uses
    on = edit_graph()
    on['lat']['inputs']['switch'] = True
    assert 'empty' in cf.live(on) and cf.describe(on)['shape'] == 'square' and cf.reshape(on, 'landscape')
    assert on['empty']['inputs']['width'] == 1248 and 'Images 1 (1 to 4)' in cf.controls_line(cf.describe(on)['extras'])


# -- room for several input pictures ---------------------------------------------------------

EDIT_INFO = {
    'TextEncodeQwenImage21': {'input': {'required': {'prompt': ['STRING', {}], 'images': ['COMFY_AUTOGROW_V3', {'template': {'min': 0}}]},
                                        'optional': {'vae': ['VAE', {}]}}},
    'BatchImagesNode': {'input': {'required': {'images': ['COMFY_AUTOGROW_V3', {'template': {'min': 1, 'max': 50}}]}}},
    'ImageCompare': {'input': {'required': {'compare_view': ['IMAGECOMPARE', {}]}, 'optional': {'image_a': ['IMAGE', {}], 'image_b': ['IMAGE', {}]}}},
    'ImageScale': {'input': {'required': {'image': ['IMAGE', {}], 'width': ['INT', {}], 'height': ['INT', {}]}}},
    'ImageBlend': {'input': {'required': {'image1': ['IMAGE', {}], 'image2': ['IMAGE', {}]}}},
    'VAEEncode': {'input': {'required': {'pixels': ['IMAGE', {}], 'vae': ['VAE', {}]}}},
}


def test_comfy_says_which_inputs_may_stay_unwired(server):
    server.info = EDIT_INFO
    c = cf.Comfy()
    assert c.can_drop('ImageCompare', 'image_a') and not c.can_drop('ImageCompare', 'compare_view')
    assert c.can_drop('TextEncodeQwenImage21', 'images.image_2', 1) and c.can_drop('TextEncodeQwenImage21', 'images.image_1', 0)
    assert c.can_drop('BatchImagesNode', 'images.image1', 1) and not c.can_drop('BatchImagesNode', 'images.image0', 0)    # one must stay
    assert not c.can_drop('ImageBlend', 'image2') and not c.can_drop('NoSuchNode', 'image') and not c.can_drop('TextEncodeQwenImage21', 'prompt')
    assert len([call for call in server.calls if call[1].startswith('/object_info/')]) == 5       # each class is asked once


def test_a_slot_nobody_filled_is_unplugged_so_the_saved_picture_never_rides_along(server):
    server.info = EDIT_INFO
    drop = cf.Comfy().can_drop
    g = edit_graph()
    g['batch'] = node('BatchImagesNode', **{'images.image0': ['a', 0], 'images.image1': ['b', 0]})
    g['enc']['inputs']['hint'] = ['batch', 0]
    assert cf.image_slots(g) == ['a', 'b'] and cf.describe(g)['image_slots'] == 2
    one, notes = cf.apply(g, {}, 'swap the shirt', 1, 'mine.png', drop)
    assert one['a']['inputs']['image'] == 'mine.png' and notes == []
    assert 'images.image_2' not in one['enc']['inputs'] and one['enc']['inputs']['images.image_1'] == ['a', 0]
    assert one['batch']['inputs'] == {'images.image0': ['a', 0]} and 'b' not in cf.live(one)        # the shirt feeds nothing now
    two, notes = cf.apply(g, {}, 'swap the shirt', 1, ['mine.png', 'shirt2.png'], drop)
    assert (two['a']['inputs']['image'], two['b']['inputs']['image']) == ('mine.png', 'shirt2.png') and notes == []
    assert two['enc']['inputs']['images.image_2'] == ['b', 0]
    none, notes = cf.apply(g, {}, 'x', 1, None, drop)                                                # no picture at all: the file as saved, and said
    assert none['enc']['inputs']['images.image_2'] == ['b', 0]
    assert notes == ["No input picture was passed, so this used the workflow's saved ones ('person.png', 'shirt.png'). To use yours, pass image."]
    blind, notes = cf.apply(g, {}, 'x', 1, 'mine.png')                                               # nobody to ask what may stay unwired
    assert blind['enc']['inputs']['images.image_2'] == ['b', 0]
    assert notes == ["Slot 2 of this workflow can't be left empty, so its saved picture ('shirt.png') went in too."]
    picked = cf.apply(g, {'image_node': 'b'}, 'x', 1, 'mine.png', drop)[0]                           # a person's pick is the first slot
    assert picked['b']['inputs']['image'] == 'mine.png' and 'images.image_1' not in picked['enc']['inputs']
    # a resize between the spare loader and its slot goes with it; a slot the picture is blended through cannot go
    via = edit_graph()
    via['fit'] = node('ImageScale', image=['b', 0], width=1024, height=1024)
    via['enc']['inputs']['images.image_2'] = ['fit', 0]
    assert 'images.image_2' not in cf.apply(via, {}, 'x', 1, 'mine.png', drop)[0]['enc']['inputs']
    blend = _img2img({'40': node('LoadImage', 'Second picture', image='b.png'), '41': node('ImageBlend', image1=['30', 0], image2=['40', 0]),
                      '31': node('VAEEncode', pixels=['41', 0], vae=['4', 2])})
    kept, notes = cf.apply(blend, {}, 'x', 1, 'mine.png', drop)
    assert kept['41']['inputs']['image2'] == ['40', 0] and kept['3']['inputs']['latent_image'] == ['31', 0]
    assert notes == ["Slot 2 of this workflow can't be left empty, so its saved picture ('b.png') went in too."]


def test_the_tool_and_the_page_take_one_picture_or_several(comfy, tool, api, server, monkeypatch):
    from core import images as ci
    api.JOBS.clear()
    server.info = EDIT_INFO
    cf.write('edit', edit_graph())
    cf.write('clip', video_graph())
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    asked = []
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: asked.append(src) or SimpleNamespace(data=png(color=(len(asked), 0, 0))))
    text, ok = tool.execute('comfy_list_workflows', {}, None, {})
    assert 'edit: image, takes up to 2 input images' in text and 'clip (default): video, takes an input image,' in text
    result, ok = run(tool, prompt='x', workflow='edit', image='img:a')
    assert ok and 'images.image_2' not in server.graphs[-1]['enc']['inputs'] and 'kept the picture' not in result['text']
    result, ok = run(tool, prompt='x', workflow='edit', image='img:a, img:b')
    assert ok and asked[-2:] == ['img:a', 'img:b'] and server.graphs[-1]['enc']['inputs']['images.image_2'] == ['b', 0]
    assert server.graphs[-1]['a']['inputs']['image'] != server.graphs[-1]['b']['inputs']['image']
    result, ok = run(tool, prompt='x', workflow='edit', image=['img:a', 'img:b', 'img:c'])
    assert not ok and 'at most 2 input images' in result
    result, ok = run(tool, prompt='x', workflow='clip', image='https://x.test/a,b.png')         # one slot: a comma is part of the name
    assert ok and asked[-1] == 'https://x.test/a,b.png'
    sent = len(server.graphs)
    async def read_one(size=-1):
        return png(color=(9, 9, 9))
    async def read_two(size=-1):
        return png(color=(8, 8, 8))
    files = [SimpleNamespace(read=read_one, filename='1.png'), SimpleNamespace(read=read_two, filename='2.png')]
    form = SimpleNamespace(get=lambda k: {'workflow': 'edit', 'prompt': 'x'}.get(k), getlist=lambda k: files if k == 'image' else [])
    async def get_form():
        return form
    job = asyncio.run(api.run(request=SimpleNamespace(form=get_form, headers={}), settings={'comfy_url': 'http://127.0.0.1:8188'}))
    assert _finished(api, job)['state'] == 'done' and server.graphs[sent]['enc']['inputs']['images.image_2'] == ['b', 0]


# -- found by the second hunt of 2026-11-22 ------------------------------------------------

def test_unplugging_a_spare_slot_never_takes_her_picture_or_a_harmless_branch(server):
    server.info = {**EDIT_INFO, 'ImageBatch': {'input': {'required': {'image1': ['IMAGE', {}], 'image2': ['IMAGE', {}]}}},
                   'PreviewImage': {'input': {'required': {'images': ['IMAGE', {}]}}}}
    drop = cf.Comfy().can_drop
    joined = edit_graph()                            # both pictures meet in a combiner before the model sees them
    joined['bat'] = node('ImageBatch', image1=['a', 0], image2=['b', 0])
    joined['enc']['inputs'] = {'prompt': 'old', 'clip': ['c', 0], 'images.image_1': ['bat', 0]}
    g, notes = cf.apply(joined, {}, 'x', 1, 'mine.png', drop)
    assert g['enc']['inputs']['images.image_1'] == ['bat', 0] and g['bat']['inputs']['image1'] == ['a', 0]      # hers is still in
    assert notes == ["Slot 2 of this workflow can't be left empty, so its saved picture ('shirt.png') went in too."]
    seen = edit_graph()                              # the spare picture is also shown in a viewer: that costs nothing
    seen['pv'] = node('PreviewImage', images=['b', 0])
    g, notes = cf.apply(seen, {}, 'x', 1, 'mine.png', drop)
    assert 'images.image_2' not in g['enc']['inputs'] and g['pv']['inputs']['images'] == ['b', 0] and notes == []
    off = edit_graph()                               # ...or it also feeds the switched-off side of a switch, or a leftover branch
    off.update({'ve': node('VAEEncode', pixels=['b', 0], vae=['v', 0]), 'stray': node('ImageScale', image=['b', 0], width=64, height=64)})
    off['lat']['inputs']['on_true'] = ['ve', 0]
    g, notes = cf.apply(off, {}, 'x', 1, 'mine.png', drop)
    assert 'images.image_2' not in g['enc']['inputs'] and g['ve']['inputs']['pixels'] == ['b', 0] and notes == []
    assert cf.apply(edit_graph(), {}, 'x', 1, '', drop)[0]['a']['inputs']['image'] == 'person.png'              # an empty name is no picture


def test_slots_are_filled_in_the_order_the_model_reads_them():
    g = edit_graph()                                 # the clothing loader was made first: a lower id, the second input
    g['5'], g['9'] = node('LoadImage', image='shirt.png'), node('LoadImage', image='person.png')
    del g['a'], g['b']
    g['enc']['inputs'].update({'images.image_1': ['9', 0], 'images.image_2': ['5', 0]})
    g['cmp']['inputs']['image_a'] = ['9', 0]
    assert cf.image_slots(g) == ['9', '5']
    two = cf.apply(g, {}, 'x', 1, ['person-mine.png', 'shirt-mine.png'])[0]
    assert (two['9']['inputs']['image'], two['5']['inputs']['image']) == ('person-mine.png', 'shirt-mine.png')
    via = edit_graph()                               # a resize on the way does not hide which input a picture ends in
    via.update({'fa': node('ImageScale', image=['a', 0], width=8, height=8), 'fb': node('ImageScale', image=['b', 0], width=8, height=8)})
    via['enc']['inputs'].update({'images.image_1': ['fb', 0], 'images.image_2': ['fa', 0]})
    via['cmp']['inputs'].pop('image_a')
    assert cf.image_slots(via) == ['b', 'a'] and cf.image_slots(via, {'image_node': 'a'}) == ['a', 'b']


def test_a_workflow_switched_to_text_only_takes_no_picture(comfy, tool, server, monkeypatch):
    from core import images as ci
    g = video_graph()                                # image-to-video with a "text to video" switch that passes the picture by
    g['t2v'] = node('PrimitiveBoolean', 'Switch to Text to Video?', value=False)
    g['i2v'] = node('LTXVImgToVideoInplace', vae=['v', 0], image=['img', 0], latent=['empty', 0], bypass=['t2v', 0])
    g['empty'] = node('EmptyLTXVLatentVideo', width=768, height=512, length=121)
    g.update({'guide': node('CFGGuider', model=['u', 0], positive=['cond', 0], negative=['cond', 1], cfg=1.0),
              'samp': node('SamplerCustomAdvanced', noise=['noise', 0], guider=['guide', 0], latent_image=['i2v', 0])})
    assert cf.image_slots(g) == ['img'] and cf.describe(g)['takes_image']
    cf.write('clip', g)
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: SimpleNamespace(data=png()))
    result, ok = run(tool, prompt='a river', extras={'Switch to Text to Video': True})
    assert ok and cf.NO_PICTURE not in str(result) and 'Switch to Text to Video on' in str(result)
    result, ok = run(tool, prompt='a river', extras={'Switch to Text to Video': True}, image='img:a')
    assert not ok and 'takes no input image the way it is set here' in result and not server.uploads
    result, ok = run(tool, prompt='a river')
    assert ok and (result['text'] if isinstance(result, dict) else result).startswith(cf.NO_PICTURE)


def test_a_setting_a_switch_brings_to_life_can_be_set_in_the_same_call():
    g = switch_graph()
    g['pe'] = node('PrimitiveBoolean', 'Boolean (Enable Prompt Enhance)', value=False)
    g['temp'] = node('PrimitiveFloat', 'Enhance Temperature', value=0.7)
    g['sw']['inputs']['switch'] = ['pe', 0]
    g['gen']['inputs']['temperature'] = ['temp', 0]
    assert 'Enhance Temperature' not in cf.controls_line(cf.controls(g))        # the rewriter is switched off
    with pytest.raises(cf.ComfyError, match="no 'Enhance Temperature'"):
        cf.set_extras(switch_graph_with(g), {}, {'Enhance Temperature': 0.9})
    done = switch_graph_with(g)
    notes = cf.set_extras(done, {}, {'Enhance Temperature': 0.9, 'Enable Prompt Enhance': True})    # whatever order she wrote them in
    assert done['temp']['inputs']['value'] == 0.9 and done['pe']['inputs']['value'] is True
    assert notes == ["Enhance Temperature 0.9 (the workflow's own is 0.7).", "Enable Prompt Enhance on (the workflow's own is off)."]


def switch_graph_with(g):
    return json.loads(json.dumps(g))


def test_only_a_spec_comfy_spells_out_lets_an_input_go(server):
    grow = lambda template: {'input': {'required': {'images': ['COMFY_AUTOGROW_V3', {'template': template} if template is not None else {}]}}}
    server.info = {'A': grow({'min': '1'}), 'B': grow(None), 'C': grow(['x']), 'D': grow({'min': True}), 'E': grow({'min': 0}), 'F': grow({})}
    c = cf.Comfy()
    assert [c.can_drop(k, 'images.image_1', 1) for k in 'ABCDEF'] == [False, False, False, False, True, False]


def test_pictures_are_all_opened_before_one_is_sent_and_named_when_one_is_bad(server):
    client = cf.Comfy()
    with pytest.raises(cf.ComfyError) as said:
        cf.plan(client, edit_graph(), {}, 'edit', 'x', 1, picture=[png(), b'not a picture'])
    assert str(said.value).startswith("Picture 2: That isn't a picture ComfyUI can take (PNG, JPEG") and '0x' not in str(said.value)
    assert not server.uploads                        # the good one was not sent either
    with pytest.raises(cf.ComfyError, match="^That isn't a picture"):
        cf.plan(client, video_graph(), {}, 'clip', 'x', 1, picture=b'nope')
    run_, notes, shaped = cf.plan(client, edit_graph(), {}, 'edit', 'x', 1, picture=[png(size=(60, 90)), png(size=(90, 60))])
    assert shaped[3] == 'Your first picture' and cf.shape_note(*shaped, (600, 900)) == 'Your first picture is 60x90: the result takes its shape.'
    assert cf.plan(client, video_graph(), {}, 'clip', 'x', 1, picture=png())[2][3] == 'Your picture'
    assert len({cf.keep('w', '.png').name for _ in range(200)}) == 200


def test_a_picture_argument_she_gets_wrong_is_said_before_anything_is_fetched(comfy, tool, server, monkeypatch):
    from core import images as ci
    cf.write('edit', edit_graph())
    cf.write('clip', video_graph())
    asked = []
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: asked.append(src) or SimpleNamespace(data=png()))
    for bad in ({'id': 'img:p'}, 5, ['img:p', {'x': 1}], ['img:p', ''], 'the picture above', ['img:p', 'jacket.png']):
        result, ok = run(tool, prompt='x', workflow='edit', image=bad)
        assert not ok and 'img:<id>, doc:<N>, a path or a URL' in result, bad
    result, ok = run(tool, prompt='x', workflow='clip', image='img:p, img:j')
    assert not ok and result == 'This workflow takes one input image.'
    cf.write('fixed', _img2img({}))
    result, ok = run(tool, prompt='x', workflow='fixed', shape='square', image='img:p')        # no size to set: said before the picture is fetched
    assert not ok and 'makes its own shape' in result
    assert not asked and not server.uploads and not server.graphs
    monkeypatch.setattr(ci, 'resolve', lambda src, **k: (_ for _ in ()).throw(ci.ImageError("'" + 'x' * 5000 + "' is not a handle")))
    result, ok = run(tool, prompt='x', workflow='clip', image='https://x.test/a.png')
    assert not ok and len(result) <= 300


def test_the_page_gets_its_job_at_once_and_the_sending_is_the_jobs_first_step(comfy, api, server):
    api.JOBS.clear()
    cf.write('clip', video_graph())
    job = _run(api, b'this is not a picture', workflow='clip', prompt='she waves')
    assert isinstance(job, dict) and job['id'] and job['phase'] == 'sending'     # a job, not a refusal: the picture is its business
    done = _finished(api, job)
    assert done['state'] == 'failed' and done['error'].startswith("That isn't a picture ComfyUI can take") and not server.graphs
    ok = _finished(api, _run(api, png(), workflow='clip', prompt='she waves'))
    assert ok['state'] == 'done' and ok['phase'] == 'working'



# -- a clip made in a private chat (isolation hunt F4, Krem's ruling 2026-11-26) ------------
# It stays a plain file: nothing is encrypted. Its name says where it was made, she is told
# it is not protected, and the app's own pages put it away while the vault is locked.

def _vault(monkeypatch, exists=True, unlocked=False):
    from core import prompt_vault
    monkeypatch.setattr(prompt_vault, 'vault_status', lambda: {'exists': exists, 'unlocked': unlocked})


def _private_clip(tool, server, monkeypatch):
    cf.write('clip', video_graph())
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b'mp4'
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    monkeypatch.setattr(tool, '_private', lambda: True)
    monkeypatch.setattr(tool, '_guard_private', lambda url, source: None)
    return run(tool, prompt='x')


def test_a_private_chats_clip_is_marked_and_she_is_told_it_is_a_plain_file(comfy, tool, server, monkeypatch):
    result, ok = _private_clip(tool, server, monkeypatch)
    assert ok
    kept = list(cf.OUTPUT_DIR.iterdir())
    assert len(kept) == 1 and kept[0].stem.endswith(cf.PRIVATE_MARK) and kept[0].suffix == '.mp4'
    assert kept[0].read_bytes() == b'mp4', "a plain file, as it always was"
    assert 'not encrypted' in result and 'user/comfyui/outputs' in result


def test_an_ordinary_chats_clip_is_not_marked_or_remarked_on(comfy, tool, server, monkeypatch):
    cf.write('clip', video_graph())
    server.outputs = {'save': {'images': [{'filename': 'c.mp4', 'type': 'output'}], 'animated': [True]}}
    server.files['c.mp4'] = b'mp4'
    monkeypatch.setattr(cf, 'stills', lambda path, n=3: ([], None))
    result, ok = run(tool, prompt='x')
    assert ok and 'not encrypted' not in result
    assert not list(cf.OUTPUT_DIR.iterdir())[0].stem.endswith(cf.PRIVATE_MARK)
    _vault(monkeypatch)
    assert len(cf.recent()) == 1, "and a locked vault puts nothing of it away"


def test_the_apps_pages_put_it_away_while_the_vault_is_locked(comfy, tool, server, api, monkeypatch):
    _private_clip(tool, server, monkeypatch)
    name = list(cf.OUTPUT_DIR.iterdir())[0].name
    _vault(monkeypatch, unlocked=True)
    assert [f['name'] for f in cf.recent()] == [name]
    assert not isinstance(api.file(name=name), tuple), "played while the vault is open"
    _vault(monkeypatch, unlocked=False)
    assert cf.recent() == [] and api.outputs()['files'] == []
    assert api.file(name=name)[1] == 404, "the same answer as a clip that is gone"
    assert api.outputs()['count'] == 1, "the space it takes is still counted, and Delete all still clears it"
    _vault(monkeypatch, exists=False)
    assert len(cf.recent()) == 1, "no vault: nothing is hidden anywhere, so not here either"


def test_cannot_tell_puts_a_private_clip_away(comfy, monkeypatch):
    from core import prompt_vault

    def broken():
        raise RuntimeError("vault unreadable")
    monkeypatch.setattr(prompt_vault, 'vault_status', broken)
    assert cf.put_away(f'clip-20261126-120000-abcd1234{cf.PRIVATE_MARK}.mp4') is True
    assert cf.put_away('clip-20261126-120000-abcd1234.mp4') is False
