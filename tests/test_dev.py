"""Exercise updater safeguards using local Git repositories; no GitHub requests."""
from pathlib import Path
import subprocess
import pytest
import dev


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True,stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def checkouts(tmp_path):
    remote=tmp_path/'remote.git';subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    source=tmp_path/'source';source.mkdir();git(source,'init','-b',dev.BRANCH)
    for k,v in [('user.name','Test'),('user.email','test@example.invalid')]:git(source,'config',k,v)
    (source/'file.txt').write_text('initial')
    git(source,'add','file.txt');git(source,'commit','-m','initial');git(source,'remote','add','origin',str(remote));git(source,'push','origin',dev.BRANCH)
    local=tmp_path/'local';subprocess.run(['git','clone','-b',dev.BRANCH,str(remote),str(local)],check=True,capture_output=True)
    return source,local


def test_update_fast_forwards(checkouts):
    source,local=checkouts
    (source/'file.txt').write_text('updated')
    git(source,'commit','-am','update');git(source,'push','origin',dev.BRANCH)
    dev.update_checkout(local)
    assert (local/'file.txt').read_text()=='updated'


def test_update_refuses_local_edits(checkouts):
    _,local=checkouts
    (local/'file.txt').write_text('my edits')
    before=git(local,'rev-parse','HEAD')
    with pytest.raises(RuntimeError,match='Local changes'):dev.update_checkout(local)
    assert (local/'file.txt').read_text()=='my edits'
    assert git(local,'rev-parse','HEAD')==before


def test_update_leaves_custom_branch_untouched(checkouts):
    _,local=checkouts
    git(local,'switch','-c','my-interface')
    with pytest.raises(RuntimeError,match='Your branch was not changed'):dev.update_checkout(local)
    assert git(local,'branch','--show-current')=='my-interface'
